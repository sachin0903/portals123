<script lang="ts">
	// This client's outbound email — the shared MessageStream, scoped to the record.
	//
	// A thin mount, like the Events tab beside it: the loader owns page one and this owns the
	// filter/paging fetches, which go through `…/emails/more` so the client scope is re-applied
	// server-side on every one of them rather than trusted to the query this page builds.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { ListingSkeleton, LoadErrorBanner, Streamed, type DataTableSort } from '@graphene/ui';
	import { MessageStream } from '@graphene/ui/comms';
	import type { CommsFilters, CommsMessage, CommsPage } from '@graphene/ui/comms';
	import { PAGE_SIZE } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The route's own param, not `data.client.uid`: the proxy this addresses lives under
	// `clients/[uid]/emails/more`, so this has to be the segment the browser is actually on.
	const base = $derived(`/clients/${page.params.uid}/emails`);

	/** Filters → the API's own query. One place, so this page's spelling and the proxy's cannot
	 *  drift — the rule the firm-wide feed follows. `contact` is deliberately absent: it is the
	 *  proxy's to set, so no filter built here can widen the scope. */
	function query(filters: CommsFilters, extra: Record<string, string | number> = {}) {
		const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
		if (filters.status.length) params.set('status', filters.status.join(','));
		if (filters.templateCode.length) params.set('template_code', filters.templateCode.join(','));
		if (filters.category.length) params.set('category', filters.category.join(','));
		if (filters.eventType.length) params.set('event_type', filters.eventType.join(','));
		if (filters.search.trim()) params.set('q', filters.search.trim());
		if (filters.since) params.set('since', filters.since);
		if (filters.until) params.set('until', filters.until);
		for (const [k, v] of Object.entries(extra)) params.set(k, String(v));
		return params;
	}

	/** One page of the feed. The endpoint's OWN `has_more` is carried back, not inferred from the
	 *  row count: a filter matching exactly three messages returns three rows and would otherwise
	 *  offer "Load more" on a complete list. */
	async function fetchPage(
		filters: CommsFilters,
		extra: Record<string, string | number> = {}
	): Promise<CommsPage> {
		const res = await fetch(`${base}/more?${query(filters, extra)}`);
		if (!res.ok) throw new Error('Could not load messages');
		const body = (await res.json()) as { items: CommsMessage[]; has_more?: boolean };
		return { items: body.items, hasMore: body.has_more ?? false };
	}

	// Nothing when the feed is in its default order — that one keysets on `id`.
	const sortParams = (sort: DataTableSort | null): Record<string, string | number> =>
		sort ? { sort: sort.key, dir: sort.dir } : {};
</script>

<svelte:head><title>Emails · {data.clientName}</title></svelte:head>

<!-- Fill the shell inset below the topbar and clip: the PAGE never scrolls, only the feed does —
     the same viewport-lock the Events tab and the firm-wide feed use. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<Streamed data={data.feed} onretry={() => invalidateAll()}>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(feed)}
			{#if feed.apiError}
				<LoadErrorBanner message={feed.apiError} onRetry={() => invalidateAll()} />
			{/if}

			<div class="flex min-h-0 flex-1 flex-col">
				<MessageStream
					messages={feed.messages}
					facets={feed.facets ?? undefined}
					hasMore={feed.hasMore}
					loadError={!!feed.apiError}
					sendingDisabled={feed.sendingDisabled}
					messageHref={(uid) => `${base}/${uid}`}
					onOpen={(uid) => goto(`${base}/${uid}`)}
					onFilterApply={(filters, sort) => fetchPage(filters, sortParams(sort))}
					onLoadMore={(cursor, filters, sort) =>
						fetchPage(
							filters,
							sort ? { ...sortParams(sort), offset: cursor.offset } : { before: cursor.before }
						)}
					onRefresh={() => invalidateAll()}
				/>
			</div>
		{/snippet}
	</Streamed>
</div>
