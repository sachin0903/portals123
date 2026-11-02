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

	// FILTERS ARE APPLIED BY THE API AND FETCHED IN PLACE — the firm filter included. The component owns
	// the controls and hands them here; this page owns the fetch. The two decisions behind that:
	//
	//   * SERVER-SIDE, so a filter covers the WHOLE stream rather than the fifty rows that happen to be
	//     loaded. On an evidence surface those two answers must not be spelled the same way, so a
	//     dropdown change costs a round trip and that is the accepted price.
	//   * NOTHING IN THE URL: no navigation, no shallow history write, so the address bar stays clean
	//     and a reload resets to unfiltered. This page therefore never calls `goto` for a filter — it
	//     swaps the rows under the toolbar instead.
	//
	// The loader reads the option vocabulary ONCE (`facets`) and it is deliberately not re-read per
	// filter. That is what retired the old `appliedTenant`/`seenFilters` bookkeeping: it existed only
	// because applying the FIRM in place made a re-read describe a different firm's stream, which would
	// have put the previous firm's people in this firm's actor dropdown. A vocabulary that never moves
	// cannot go stale.

	/** One page of the stream as the `more` proxy answers it — structurally the component's own
	 *  `StreamPage`, which lives in its module block and is not on `@graphene/ui`'s public surface
	 *  yet, so a shape change fails to assign rather than reaching the table as `undefined` rows. */
	type EventPage = { events: ActionEvent[]; hasMore: boolean };

	// One fetcher for both jobs below. The proxy holds the session token and owns the failure contract —
	// a non-2xx is a real failure, never an empty page that would read as the end of the trail — and
	// `null` is what the component takes for "keep the rows and offer a retry".
	async function fetchPage(filters: ActionLogFilters, before?: number): Promise<EventPage | null> {
		try {
			const res = await fetch(`/activity/more?${actionLogBrowserQuery(filters, { before })}`);
			if (!res.ok) return null;
			return (await res.json()) as EventPage;
		} catch {
			// A dropped connection is the same answer as a refusal: a failure to report, not an empty
			// page. Caught here so a network error cannot escape as an unhandled rejection.
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
			const res = await fetch(`/activity/more?${actionLogBrowserQuery(filters, { after })}`);
			if (!res.ok) return null;
			return ((await res.json()) as EventPage).events;
		} catch {
			// Silent: the reader did not ask for this fetch, so a failure must not become a banner.
			return null;
		}
	}

	// Refresh / retry re-runs the load: the UNFILTERED first page, the firm lookup and a fresh
	// vocabulary — the entry state. No URL bookkeeping, because there is none to keep, and the
	// operator's filters live in the component, which re-applies them from there.
	const reload = () => invalidateAll();

	// Entities link into the tenant BOOK, scoped by the firm the event touched (the proxied TARGET
	// when there is one, else the acting tenant). The shared map holds which kinds book can show.
	function actionEntityHref(e: ActionEvent, entity: ActionEntity): string | null {
		const uid = e.target_tenant_uid ?? e.tenant_uid;
		if (!uid) return null;
		return bookEntityHref(uid, entity);
	}
</script>

<!-- Fill the inset below the topbar (the shell exposes --topbar-h): the page never scrolls, only
     the two panes do. The negative margin cancels the shell content padding so the fixed-height box
     fills the area exactly, then re-adds its own padding inside. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-3 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<PageHeader
		eyebrow="Internal operations"
		title="Events"
		description="Every action anyone took, across all three portals, as it happened."
	/>

	{#if data.apiError}
		<LoadErrorBanner message={data.apiError} onRetry={reload} />
	{/if}

	<div class="min-h-0 flex-1">
		<!-- The loader supplies the newest page, the firm lookup and the filter vocabulary; the
		     component owns the rows from there — a filter fetch swaps them, "Load more" appends. -->
		<EventStream
			events={data.events}
			tenants={data.tenants}
			facets={data.facets ?? undefined}
			hasMore={data.hasMore}
			loadError={!!data.apiError}
			entityHref={actionEntityHref}
			onFilterApply={applyFilters}
			onLoadMore={fetchNextPage}
			onRefresh={reload}
			onPollNewer={pollNewer}
		/>
	</div>
</div>
