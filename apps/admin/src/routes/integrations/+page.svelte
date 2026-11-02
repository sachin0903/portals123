<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import {
		Alert,
		AlertDescription,
		Card,
		CardContent,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		EmptyState,
		SectionHeading,
		StatusBadge,
		sentenceCase
	} from '@graphene/ui';
	import { ListingToolbar } from '@graphene/ui/book/listing';
	import PlugIcon from '@lucide/svelte/icons/plug';
	import type { IntegrationStatus, IntegrationSummary } from '$lib/api';
	import type { PageData } from './$types';
	import IntegrationEditSheet from './IntegrationEditSheet.svelte';

	let { data }: { data: PageData } = $props();

	let query = $state('');
	const filtered = $derived(
		query.trim()
			? data.integrations.filter((row) => {
					const needle = query.trim().toLowerCase();
					return (
						(row.platform ?? '').toLowerCase().includes(needle) ||
						(row.firm_description ?? '').toLowerCase().includes(needle) ||
						row.tenant_name.toLowerCase().includes(needle)
					);
				})
			: data.integrations
	);

	const columns: DataTableColumn<IntegrationSummary>[] = [
		{ key: 'platform', header: 'Platform', sortable: true, sortValue: (r) => r.platform ?? '' },
		{
			key: 'firm_description',
			header: 'Firm',
			sortable: true,
			sortValue: (r) => r.firm_description ?? ''
		},
		{ key: 'tenant_name', header: 'Tenant', sortable: true },
		{ key: 'status', header: 'Status', sortable: true, sortValue: (r) => r.status },
		{ key: 'enabled', header: 'Enabled', sortable: true, sortValue: (r) => (r.enabled ? 1 : 0) },
		{
			key: 'installed',
			header: 'Installed',
			sortable: true,
			sortValue: (r) => (r.installed ? 1 : 0)
		}
	];
	let sort = $state<DataTableSort | null>(null);

	const STATUS_TONE: Record<IntegrationStatus, 'success' | 'warning' | 'danger'> = {
		approved: 'success',
		pending: 'warning',
		rejected: 'danger'
	};

	let sheetOpen = $state(false);
	let editing = $state<IntegrationSummary | null>(null);

	function openEdit(row: IntegrationSummary) {
		editing = row;
		sheetOpen = true;
	}
</script>

<svelte:head><title>Integrations · Internal operations</title></svelte:head>

<div class="space-y-5">
	<div>
		<h1 class="text-lg font-semibold">Integrations</h1>
		<p class="text-muted-foreground text-sm">
			SEI custodian/software platform approvals, DSR grants and feature toggles.
		</p>
	</div>

	{#if data.apiError}
		<Alert variant="destructive">
			<AlertDescription>{data.apiError}</AlertDescription>
		</Alert>
	{/if}

	{#if data.integrations.length === 0 && !data.apiError}
		<EmptyState
			icon={PlugIcon}
			title="No integrations yet"
			description="Platform integration requests appear here once a firm applies for one."
		/>
	{:else}
		<section>
			<SectionHeading title="Integrations" count={data.integrations.length} />
			<Card glass>
				<CardContent class="space-y-4">
					<ListingToolbar
						bind:query
						placeholder="Search platform, firm or tenant…"
						dirty={query.trim() !== ''}
						onclear={() => (query = '')}
					/>
					<DataTable
						{columns}
						rows={filtered}
						bind:sort
						getKey={(r) => r.uid}
						label="Integrations"
						onRowClick={openEdit}
						empty={query.trim() ? `No integrations match “${query.trim()}”.` : 'No integrations.'}
					>
						{#snippet cell(row, column)}
							{#if column.key === 'platform'}
								<a
									href="/integrations/{row.uid}"
									class="font-medium hover:underline"
									onclick={(e) => e.stopPropagation()}
								>
									{row.platform ?? '—'}
								</a>
							{:else if column.key === 'firm_description'}
								{row.firm_description ?? '—'}
							{:else if column.key === 'tenant_name'}
								{row.tenant_name}
							{:else if column.key === 'status'}
								<StatusBadge tone={STATUS_TONE[row.status]}>{sentenceCase(row.status)}</StatusBadge>
							{:else if column.key === 'enabled'}
								<span class="text-sm">{row.enabled ? 'Yes' : 'No'}</span>
							{:else if column.key === 'installed'}
								<span class="text-sm">{row.installed ? 'Yes' : 'No'}</span>
							{/if}
						{/snippet}
					</DataTable>
				</CardContent>
			</Card>
		</section>
	{/if}
</div>

<IntegrationEditSheet
	bind:open={sheetOpen}
	integration={editing}
	firms={data.firms}
	features={data.features}
	canWrite={data.canWrite}
	formAction="?/update"
	onSaved={() => void invalidateAll()}
/>
