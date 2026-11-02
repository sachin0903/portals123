<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { LoadErrorBanner, PageHeader, type DataTableSort } from '@graphene/ui';
	import {
		MessageStream,
		type CommsFilters,
		type CommsMessage,
		type CommsPage
	} from '@graphene/ui/comms';
	import { PAGE_SIZE } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	async function page(
		filters: CommsFilters,
		extra: Record<string, string | number> = {}
	): Promise<CommsPage> {
		const q = new URLSearchParams({ limit: String(PAGE_SIZE) });
		if (filters.status.length) q.set('status', filters.status.join(','));
		if (filters.search.trim()) q.set('q', filters.search.trim());
		for (const [k, v] of Object.entries(extra)) q.set(k, String(v));
		const r = await fetch(`page?${q}`);
		if (!r.ok) throw new Error('load failed');
		const b = (await r.json()) as { items: CommsMessage[]; has_more: boolean };
		return { items: b.items, hasMore: b.has_more };
	}

	/** NOTHING when unsorted: naming the default order would trade its keyset for an offset page. */
	const sortParams = (sort: DataTableSort | null): Record<string, string | number> =>
		sort ? { sort: sort.key, dir: sort.dir } : {};
</script>

<PageHeader
	title="Emails"
	description="Every email this firm sent and its delivery outcome."
/>{#if data.apiError}<LoadErrorBanner message={data.apiError} />{/if}<MessageStream
	messages={data.messages}
	facets={data.facets ?? undefined}
	hasMore={data.hasMore}
	loadError={!!data.apiError}
	sendingDisabled={data.sendingDisabled}
	messageHref={(uid) => `/tenants/${data.tenantUid}/operate/emails/${uid}`}
	onOpen={(uid) => goto(`/tenants/${data.tenantUid}/operate/emails/${uid}`)}
	onFilterApply={(f, sort) => page(f, sortParams(sort))}
	onLoadMore={(cursor, f, sort) =>
		page(f, sort ? { ...sortParams(sort), offset: cursor.offset } : { before: cursor.before })}
	onRefresh={() => invalidateAll()}
	initialFilters={data.eventType ? { eventType: [data.eventType] } : undefined}
/>
