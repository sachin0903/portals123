<script lang="ts">
	// This client's outbound email — the shared MessageStream, scoped to the record, as on the
	// platform. Paging and filters go through `./page`, which re-applies the client scope.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		ListingSkeleton,
		LoadErrorBanner,
		PageHeader,
		Streamed,
		type DataTableSort
	} from '@graphene/ui';
	import { MessageStream } from '@graphene/ui/comms';
	import type { CommsFilters, CommsMessage, CommsPage } from '@graphene/ui/comms';
	import { PAGE_SIZE } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const base = $derived(
		`/tenants/${page.params.uid}/operate/clients/${page.params.clientUid}/emails`
	);
	const messageHref = (uid: string) => `/tenants/${page.params.uid}/operate/emails/${uid}`;

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

	async function fetchPage(
		filters: CommsFilters,
		extra: Record<string, string | number> = {}
	): Promise<CommsPage> {
		const res = await fetch(`${base}/page?${query(filters, extra)}`);
		if (!res.ok) throw new Error('Could not load messages');
		const body = (await res.json()) as { items: CommsMessage[]; has_more?: boolean };
		return { items: body.items, hasMore: body.has_more ?? false };
	}

	const sortParams = (sort: DataTableSort | null): Record<string, string | number> =>
		sort ? { sort: sort.key, dir: sort.dir } : {};
</script>

<svelte:head><title>Emails · {data.clientName}</title></svelte:head>

<div
	class="-m-4 flex min-w-0 flex-col gap-3 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<PageHeader
		title="Emails"
		description="Every email sent to this client, and what happened to it after it left."
	/>

	<Streamed data={data.feed} onretry={() => invalidateAll()}>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(feed)}
			{#if feed.apiError}
				<LoadErrorBanner message={feed.apiError} onRetry={() => invalidateAll()} />
			{/if}
			<div class="min-h-0 flex-1">
				<MessageStream
					messages={feed.messages}
					facets={feed.facets ?? undefined}
					hasMore={feed.hasMore}
					loadError={!!feed.apiError}
					sendingDisabled={feed.sendingDisabled}
					{messageHref}
					onOpen={(uid) => goto(messageHref(uid))}
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
