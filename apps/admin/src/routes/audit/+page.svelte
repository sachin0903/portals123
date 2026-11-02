<script lang="ts">
	// The record-change trail (audit.log), as a master-detail surface matching the Events page. The
	// component owns the layout, toolbar and load-more; this page owns navigation, data loading and
	// the CSV export. Filters live in the URL so a search covers the whole trail (and exportHref
	// tracks them).
	import { goto, invalidateAll } from '$app/navigation';
	import { buttonVariants, LoadErrorBanner, PageHeader } from '@graphene/ui';
	import AuditLog from '$lib/audit/audit-log.svelte';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import type { AuditEvent, AuditFilters } from '$lib/audit/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function query(f: AuditFilters, extra: Record<string, string> = {}): URLSearchParams {
		const q = new URLSearchParams();
		// `actor`/`tenant_id` are ARRAYS — each value appends its own repeated param (an OR at the API).
		for (const actor of f.actor) if (actor) q.append('actor', actor);
		for (const tenantId of f.tenantId) if (tenantId) q.append('tenant_id', tenantId);
		const values: Record<string, string> = {
			action: f.action,
			since: f.since,
			until: f.until,
			...extra
		};
		for (const [key, value] of Object.entries(values)) if (value) q.set(key, value);
		return q;
	}

	function applyFilters(f: AuditFilters) {
		const q = query(f);
		goto(`/audit${q.size ? `?${q}` : ''}`, { keepFocus: true });
	}

	// "Load more" fetches the next keyset page (id < before_id) from the same-origin proxy and hands
	// it back for the shell to append in place — no navigation, no URL churn.
	async function loadMore(beforeId: number) {
		const res = await fetch(`/audit/more?${query(data.filters, { before_id: String(beforeId) })}`);
		if (!res.ok) return null;
		return (await res.json()) as { events: AuditEvent[]; hasMore: boolean };
	}

	const exportHref = $derived(`/audit/export?${query(data.filters)}`);
</script>

<!-- Fill the shell inset below the topbar and clip: the page never scrolls, only the two panes do. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-3 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<PageHeader
		eyebrow="Internal operations"
		title="Audit log"
		description="See what changed, when it changed and who made the change."
	>
		{#snippet actions()}
			<a href={exportHref} class={buttonVariants({ variant: 'secondary', size: 'sm' })} download>
				<DownloadIcon class="size-4" /> Export CSV
			</a>
		{/snippet}
	</PageHeader>

	{#if data.apiError}
		<LoadErrorBanner message={data.apiError} onRetry={() => invalidateAll()} />
	{/if}

	<div class="min-h-0 flex-1">
		<AuditLog
			events={data.events}
			tenants={data.tenants}
			filters={data.filters}
			hasMore={data.hasMore}
			onFiltersChange={applyFilters}
			onLoadMore={loadMore}
		/>
	</div>
</div>
