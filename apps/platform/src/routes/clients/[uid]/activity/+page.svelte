<script lang="ts">
	// This client's slice of the firm's action-event stream — the shared EventStream in firm posture
	// (no firm dropdown; proxied rows attributed "Graphene support · {operator}"), scoped to the
	// contact server-side.
	//
	// FILTERS ARE APPLIED BY THE API AND FETCHED IN PLACE: the component owns the controls and hands
	// them here, this page owns the fetch. Server-side because a filter must cover the client's WHOLE
	// trail rather than the fifty rows that happen to be loaded; and nothing goes in the URL — no
	// navigation, no shallow history write — so the address bar stays clean and a reload resets to
	// unfiltered.
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		EventStream,
		ListingSkeleton,
		LoadErrorBanner,
		Streamed,
		actionLogBrowserQuery,
		type ActionEntity,
		type ActionEvent,
		type ActionLogFilters
	} from '@graphene/ui';
	import { portalEntityHref } from '$lib/audit/entity-href';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The route's own param, not `data.client.id`: the proxy this addresses lives under
	// `clients/[uid]/activity/more`, so a serial here is a 404 on Load more.
	const base = $derived(`/clients/${page.params.uid}/activity`);

	/** One page of the slice as the section's `more` proxy answers it — structurally the component's
	 *  own `StreamPage` (module-block type, not yet on `@graphene/ui`'s public surface). */
	type EventPage = { events: ActionEvent[]; hasMore: boolean };

	// One fetcher for both jobs below. The section's proxy holds the session token, pins `contact` to
	// this section's id, and owns the failure contract — a non-2xx is a real failure, never an empty
	// page that would read as the end of the trail. `null` is what the component takes for "keep the
	// rows and offer a retry".
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

	// Refresh / retry re-runs the load: the UNFILTERED first page plus a fresh vocabulary, which is the
	// entry state. No URL bookkeeping is needed because there is none, and the toolbar's filters live in
	// the component, which re-applies them from there.
	const reload = () => invalidateAll();

	// A resolved entity → this client's own record pages (the same app the events belong to).
	// The shared map — the same kinds reach the same pages as on the firm-wide trail.
	const entityHref = (_event: ActionEvent, entity: ActionEntity) => portalEntityHref(entity);
</script>

<svelte:head><title>Activity · {data.clientName}</title></svelte:head>

<!-- Fill the shell inset below the topbar and clip: the PAGE never scrolls, only the two panes do
     (the same viewport-lock the firm-wide /activity page uses). -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<Streamed data={data.feed} onretry={reload}>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(feed)}
			{#if feed.apiError}
				<LoadErrorBanner message={feed.apiError} onRetry={reload} />
			{/if}

			<div class="flex min-h-0 flex-1 flex-col">
				<EventStream
					events={feed.events}
					facets={feed.facets ?? undefined}
					hasMore={feed.hasMore}
					loadError={!!feed.apiError}
					showTenantFilter={false}
					attribution="firm"
					{entityHref}
					onFilterApply={applyFilters}
					onLoadMore={fetchNextPage}
					onRefresh={reload}
				/>
			</div>
		{/snippet}
	</Streamed>
</div>
