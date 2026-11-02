<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import {
		EventStream,
		LoadErrorBanner,
		PageHeader,
		actionLogBrowserQuery,
		type ActionEntity,
		type ActionEvent,
		type ActionLogFilters
	} from '@graphene/ui';
	import { bookEntityHref } from '$lib/audit/entity-href';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const base = $derived(`/tenants/${data.tenantUid}/activity`);

	// FILTERS ARE APPLIED BY THE API AND FETCHED IN PLACE: the component owns the controls and hands them
	// here, this page owns the fetch. Server-side because a filter must cover this firm's WHOLE slice
	// rather than the fifty rows that happen to be loaded; and nothing goes in the URL — no navigation,
	// no shallow history write — so the address bar stays clean and a reload resets to unfiltered.
	//
	// The FIRM is this section's scope, pinned by the proxy from the route param, and never a filter a
	// reader could clear — which is also why the vocabulary the loader read applies for the whole
	// session: every filter narrows within one firm, so there is no firm change that could make it stale.

	/** One page of the stream as the section's `more` proxy answers it — structurally the component's
	 *  own `StreamPage` (which lives in its module block, not yet on `@graphene/ui`'s public
	 *  surface). */
	type EventPage = { events: ActionEvent[]; hasMore: boolean };

	// One fetcher for both jobs below. The section's proxy holds the session token, forces the firm to
	// this section's uid, and owns the failure contract — a non-2xx is a real failure, never an empty
	// page that would read as the end of the trail; `null` keeps the rows and offers a retry.
	async function fetchPage(filters: ActionLogFilters, before?: number): Promise<EventPage | null> {
		try {
			const res = await fetch(`${base}/more?${actionLogBrowserQuery(filters, { before })}`);
			if (!res.ok) return null;
			return (await res.json()) as EventPage;
		} catch {
			// A dropped connection is the same answer as a refusal — a failure to report, not an empty
			// page — and is caught so it cannot escape as an unhandled rejection.
			return null;
		}
	}

	/** Apply the toolbar's filters: page ONE at those filters, with no cursor, for the component to
	 *  SWAP its rows for. */
	const applyFilters = (filters: ActionLogFilters) => fetchPage(filters);

	/** "Load more" pages AT THE APPLIED FILTERS — the filters the component hands over, never the
	 *  loader's. Once a filter has been applied in place the loader's answer describes a query nobody
	 *  is looking at, so paging from it would append rows from the wrong set and still answer 200. */
	const fetchNextPage = (before: number, filters: ActionLogFilters) => fetchPage(filters, before);

	/** THE LIVE POLL. Asks the same proxy for rows NEWER than the newest one held, at the filters the
	 *  feed is showing — so what the "N new events" pill offers is exactly what clicking it reveals.
	 *  The component holds the answer and offers it; it never moves the table on its own. */
	async function pollNewer(
		after: number,
		filters: ActionLogFilters
	): Promise<ActionEvent[] | null> {
		try {
			const res = await fetch(`${base}/more?${actionLogBrowserQuery(filters, { after })}`);
			if (!res.ok) return null;
			return ((await res.json()) as EventPage).events;
		} catch {
			// Silent: the reader did not ask for this fetch, so a failure must not become a banner.
			return null;
		}
	}

	// Refresh / retry re-runs the load: the UNFILTERED first page plus a fresh vocabulary, which is the
	// entry state. Nothing to keep in step, and the filters live in the component, which re-applies them.
	const reload = () => invalidateAll();

	// Every event here belongs to this firm, so entities link into THIS firm's book.
	const actionEntityHref = (_e: ActionEvent, entity: ActionEntity) =>
		bookEntityHref(data.tenantUid, entity);
</script>

<!-- Fill the inset below the topbar (the shell exposes --topbar-h): the page never scrolls, only
     the two panes do. The negative margin cancels the shell content padding, re-added inside. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-3 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<PageHeader
		eyebrow="Firm activity"
		title="Events"
		description="Actions by the firm's staff and by Graphene support acting on its behalf."
	/>

	{#if data.apiError}
		<LoadErrorBanner message={data.apiError} onRetry={reload} />
	{/if}

	<div class="min-h-0 flex-1">
		<!-- The loader supplies the newest page and the filter vocabulary; the component owns the rows
		     from there — a filter fetch swaps them, "Load more" appends. -->
		<EventStream
			events={data.events}
			facets={data.facets ?? undefined}
			hasMore={data.hasMore}
			loadError={!!data.apiError}
			showTenantFilter={false}
			entityHref={actionEntityHref}
			onFilterApply={applyFilters}
			onLoadMore={fetchNextPage}
			onRefresh={reload}
			onPollNewer={pollNewer}
		/>
	</div>
</div>
