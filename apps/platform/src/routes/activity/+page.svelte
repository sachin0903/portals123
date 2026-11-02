<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import {
		DropdownMenu,
		EventStream,
		ListingSkeleton,
		LoadErrorBanner,
		Streamed,
		actionLogBrowserQuery,
		type ActionEntity,
		type ActionEvent,
		type ActionLogFilters
	} from '@graphene/ui';
	import { ListingOverflow } from '@graphene/ui/book/listing';
	import { portalEntityHref } from '$lib/audit/entity-href';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The shared EventStream in its FIRM posture: no firm dropdown (this is a single-firm surface —
	// the API hard-codes the scope to the caller's own tenant), and proxied rows attributed as
	// "Graphene support · {operator}" — the operator stays named, because a real person acted on
	// this firm's records.
	//
	// FILTERS ARE APPLIED BY THE API AND FETCHED IN PLACE. The component owns the controls and hands
	// them here; this page owns the fetch. The two decisions behind that:
	//
	//   * SERVER-SIDE, so a filter covers the WHOLE trail rather than the fifty rows that happen to be
	//     loaded — on a compliance record, "nobody did that" and "nobody did that on this page" are
	//     different answers and only one of them is true. A dropdown change costs a round trip, and
	//     that is the accepted price.
	//   * NOTHING IN THE URL: no navigation, no shallow history write. The address bar stays clean and
	//     a reload resets to unfiltered. So this page never calls `goto` for a filter — it swaps the
	//     rows under the toolbar instead.

	/** One page of the trail as the `more` proxy answers it — structurally the component's own
	 *  `StreamPage` (module-block type, not yet on `@graphene/ui`'s public surface). */
	type EventPage = { events: ActionEvent[]; hasMore: boolean };

	// One fetcher for both jobs below. The proxy holds the session token and owns the failure contract
	// — a non-2xx is a real failure, never an empty page that would read as the end of the trail — and
	// `null` is the answer the component takes for "keep what is on screen and offer a retry".
	async function fetchPage(filters: ActionLogFilters, before?: number): Promise<EventPage | null> {
		try {
			const res = await fetch(`/activity/more?${actionLogBrowserQuery(filters, { before })}`);
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

	// Refresh / retry is a plain load re-run. It needs no URL bookkeeping because there is none — and
	// it deliberately re-reads the UNFILTERED first page plus a fresh vocabulary, which is the entry
	// state; the toolbar's own filters live in the component and it re-applies them from there.
	const reload = () => invalidateAll();

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

	// NO `links` prop, deliberately. The raw target-id chips carry the SERIAL a path param recorded,
	// and `/clients/[uid]` and `/accounts/[uid]` resolve by uid — so building `/clients/{contact_id}`
	// from one produced a link that always 404'd. `$lib/audit/entity-href` says exactly this ("never
	// the serial the stream recorded") and the per-client trail never passed one.
	//
	// Nothing is lost: the RESOLVED entity rows below link the same records by uid, which is the
	// working path to them. A chip that reads as a link and dead-ends is worse than a chip that does
	// not, and having it on one Events surface but not the other is a bug a reader meets by accident.

	// A resolved entity row → this portal's record pages (the read-time labels the API attaches).
	// The shared map, so this surface and the per-client one link the same kinds to the same places.
	const entityHref = (_event: ActionEvent, entity: ActionEntity) => portalEntityHref(entity);
</script>

{#snippet listingActions()}
	<!-- The registry has no nav item: it is reached from the surface it configures. -->
	<ListingOverflow>
		<DropdownMenu.Item>
			{#snippet child({ props })}
				<a {...props} href="/activity/notifications">
					<SettingsIcon class="size-4" />
					Configure notifications
				</a>
			{/snippet}
		</DropdownMenu.Item>
	</ListingOverflow>
{/snippet}

<!-- Fill the inset below the topbar (the shell exposes --topbar-h) and clip: the PAGE never
     scrolls, only the two panes do. The negative margin cancels the shell content padding so the
     fixed-height box fills the area exactly, then re-adds its own padding inside. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<!-- The feed is STREAMED (`+page.server.ts`): the frame is on screen the instant the page is
	     navigated to and the rows land behind it — the same shape as the client record's Events tab.
	     `keep`, because the only later promise is Refresh/retry, and the toolbar's filters live in
	     the component: holding it mounted while the answer is re-read is what lets them survive. -->
	<Streamed data={data.feed} onretry={reload} keep>
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
					onPollNewer={pollNewer}
					actions={data.canConfigure ? listingActions : undefined}
				/>
			</div>
		{/snippet}
	</Streamed>
</div>
