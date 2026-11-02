<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { ListingSkeleton, LoadErrorBanner, Streamed, type DataTableSort } from '@graphene/ui';
	import { MessageStream } from '@graphene/ui/comms';
	import type { CommsFilters, CommsMessage, CommsPage } from '@graphene/ui/comms';
	import { PAGE_SIZE } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/**
	 * What the listing opens narrowed to, if anything.
	 *
	 * Only ever from an INBOUND `?event_type=` — the event registry's 30-day count links here for
	 * one event. The loader has already fetched page one with it applied, so this is what tells the
	 * toolbar the filter is in force; without it the rows arrived filtered while the controls read
	 * as unfiltered, which is worse than not linking at all.
	 */
	const initialFilters = $derived(data.eventType ? { eventType: [data.eventType] } : undefined);

	/** Filters → the API's own query. One place, so the loader's spelling and the proxy's cannot
	 *  drift — the rule `actionLogQuery` exists for on the logs surface. */
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

	/**
	 * One page of the feed. The endpoint's OWN `has_more` is carried back, not inferred from the
	 * row count: it is computed there with the limit+1 trick, where `items.length > 0` says
	 * something different — a filter matching exactly three messages returns three rows and would
	 * offer "Load more" on a complete list.
	 */
	async function page(
		filters: CommsFilters,
		extra: Record<string, string | number> = {}
	): Promise<CommsPage> {
		const res = await fetch(`/emails/more?${query(filters, extra)}`);
		if (!res.ok) throw new Error('Could not load messages');
		const body = (await res.json()) as { items: CommsMessage[]; has_more?: boolean };
		return { items: body.items, hasMore: body.has_more ?? false };
	}

	// Nothing when the feed is in its default order — that one keysets on `id`.
	const sortParams = (sort: DataTableSort | null): Record<string, string | number> =>
		sort ? { sort: sort.key, dir: sort.dir } : {};
</script>

<svelte:head><title>Emails</title></svelte:head>

<!-- The platform listing shell: a full-height flex column so the CARD scrolls its own rows
     rather than the page growing — the frame accounts/transactions/clients all use. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<!-- The feed is STREAMED (`+page.server.ts`): the frame is on screen the instant the page is
	     navigated to and the rows land behind it — the same shape as the client record's Activity
	     tab. `keep`, because the only later promise is Refresh/retry and the toolbar's filters live
	     in the component: holding it mounted while the answer is re-read is what lets them survive. -->
	<Streamed data={data.feed} onretry={() => invalidateAll()} keep>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(feed)}
			{#if feed.apiError}
				<LoadErrorBanner message={feed.apiError} onRetry={() => invalidateAll()} />
			{/if}

			<MessageStream
				messages={feed.messages}
				facets={feed.facets ?? undefined}
				hasMore={feed.hasMore}
				loadError={!!feed.apiError}
				sendingDisabled={feed.sendingDisabled}
				messageHref={(uid) => `/emails/${uid}`}
				clientHref={(uid) => `/clients/${uid}`}
				onOpen={(uid) => goto(`/emails/${uid}`)}
				onFilterApply={(filters, sort) => page(filters, sortParams(sort))}
				onLoadMore={(cursor, filters, sort) =>
					page(
						filters,
						sort ? { ...sortParams(sort), offset: cursor.offset } : { before: cursor.before }
					)}
				onRefresh={() => invalidateAll()}
				{initialFilters}
			/>
		{/snippet}
	</Streamed>
</div>
