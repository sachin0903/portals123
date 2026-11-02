<script lang="ts">
	// This firm's record-change trail (audit.log), as the master-detail AuditLog in its single-firm
	// posture: no firm filter, no Firm column (every row names this firm). The firm is fixed by the
	// URL; load-more reuses the top-level /audit/more proxy with an explicit tenant_id.
	import { goto, invalidateAll } from '$app/navigation';
	import { buttonVariants, LoadErrorBanner, PageHeader } from '@graphene/ui';
	import AuditLog from '$lib/audit/audit-log.svelte';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import type { AuditEvent, AuditFilters } from '$lib/audit/types';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const base = $derived(`/tenants/${data.tenantUid}/audit`);
	// AuditLog wants the full AuditFilters shape; the tenant view carries no firm filter, so tenantId
	// is always empty here (the firm is the URL).
	const filters = $derived<AuditFilters>({ ...data.filters, tenantId: [] });

	function query(
		f: Pick<AuditFilters, 'action' | 'actor' | 'since' | 'until'>,
		extra: Record<string, string> = {}
	): URLSearchParams {
		const q = new URLSearchParams();
		// `actor` is an ARRAY — each value appends its own repeated param (an OR at the API).
		for (const actor of f.actor) if (actor) q.append('actor', actor);
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
		goto(`${base}${q.size ? `?${q}` : ''}`, { keepFocus: true });
	}

	// Load-more reuses the top-level /audit/more proxy, scoped to this firm via tenant_id.
	async function loadMore(beforeId: number) {
		const q = query(data.filters, {
			before_id: String(beforeId),
			tenant_id: String(data.tenantId)
		});
		const res = await fetch(`/audit/more?${q}`);
		if (!res.ok) return null;
		return (await res.json()) as { events: AuditEvent[]; hasMore: boolean };
	}

	// The CSV export lives at the top-level /audit/export (outside the tenant section), so the firm
	// is passed explicitly as tenant_id to scope the download.
	const exportHref = $derived(
		`/audit/export?${query(data.filters, { tenant_id: String(data.tenantId) })}`
	);
</script>

<div
	class="-m-4 flex min-w-0 flex-col gap-3 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<PageHeader
		eyebrow="Firm activity"
		title="Audit log"
		description="See what changed in this firm's records, when it changed and who made the change."
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
			{filters}
			showFirm={false}
			hasMore={data.hasMore}
			onFiltersChange={applyFilters}
			onLoadMore={loadMore}
		/>
	</div>
</div>
