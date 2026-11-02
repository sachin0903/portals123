<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Badge,
		Card,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		formatGBP,
		humanise
	} from '@graphene/ui';
	import { RECON_BREAKS, RECON_FIGURES } from '$lib/contributions/data';
	import type { ReconBreak } from '$lib/contributions/types';

	const legs = [
		{ label: 'Graphene book', value: RECON_FIGURES.graphene, sub: 'net contributions identified' },
		{ label: 'SEI custody', value: RECON_FIGURES.sei, sub: 'credits posted to accounts' },
		{ label: 'Morgan Lloyd', value: RECON_FIGURES.ml, sub: 'received contributions recorded' }
	];
	const inputs = [
		{
			leg: 'Graphene book',
			source: 'Canonical ledger',
			api: 'platform.transactions / pensions.contributions'
		},
		{
			leg: 'SEI custody',
			source: 'SWP / Boxi daily credits',
			api: 'RetrieveTransactionsForClientAccount'
		},
		{ leg: 'Morgan Lloyd', source: 'SIPP scheme record', api: 'cam_getreceivedcontributions' }
	];

	// `status` is the only derived column here — everything else is a plain string field, so the
	// default row-value sort already matches what's printed. Status sorts on the humanised word
	// the badge shows, not the raw snake_case value underneath it.
	const breakColumns: DataTableColumn<ReconBreak>[] = [
		{
			key: 'subject',
			header: 'Subject',
			cellClass: 'text-brand-strong font-mono text-xs',
			sortable: true,
			sortDefault: true
		},
		{ key: 'member', header: 'Member', sortable: true },
		{ key: 'scope', header: 'Scope', cellClass: 'font-mono text-[11px]', sortable: true },
		{ key: 'type', header: 'Break type', sortable: true },
		{ key: 'detail', header: 'Detail', cellClass: 'max-w-80 text-xs', sortable: true },
		{
			key: 'ageDays',
			header: 'Age',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: (b) => humanise(b.status)
		}
	];
	let breakSort = $state<DataTableSort | null>(null);

	const inputColumns: DataTableColumn<(typeof inputs)[number]>[] = [
		{ key: 'leg', header: 'Leg', cellClass: 'font-medium', sortable: true, sortDefault: true },
		{ key: 'source', header: 'Source', sortable: true },
		{ key: 'api', header: 'API / feed', sortable: true }
	];
	let inputSort = $state<DataTableSort | null>(null);
</script>

<div class="space-y-4">
	<div class="grid gap-3 sm:grid-cols-3">
		{#each legs as leg (leg.label)}
			<Card glass class="p-4 text-center">
				<div class="text-muted-foreground text-xs font-semibold">
					{leg.label}
				</div>
				<div class="mt-1 text-2xl font-semibold tabular-nums">{formatGBP(leg.value)}</div>
				<div class="text-muted-foreground mt-1 text-xs">{leg.sub}</div>
			</Card>
		{/each}
	</div>

	<Alert>
		<AlertDescription>{RECON_FIGURES.note}</AlertDescription>
	</Alert>

	<div>
		<h4 class="mb-2 text-sm font-semibold">Open breaks ({RECON_BREAKS.length})</h4>
		<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
			<DataTable
				columns={breakColumns}
				rows={RECON_BREAKS}
				bind:sort={breakSort}
				getKey={(b) => b.subject}
				label="Open breaks"
				empty="No open breaks."
			>
				{#snippet cell(b, column)}
					{#if column.key === 'subject'}
						{b.subject}
					{:else if column.key === 'member'}
						{b.member}
					{:else if column.key === 'scope'}
						{b.scope}
					{:else if column.key === 'type'}
						<code class="text-xs">{b.type}</code>
					{:else if column.key === 'detail'}
						{b.detail}
					{:else if column.key === 'ageDays'}
						{b.ageDays}d
					{:else if column.key === 'status'}
						<Badge
							variant="outline"
							class={b.status === 'open'
								? 'bg-destructive-subtle text-destructive-strong border-destructive-line'
								: 'bg-warning-subtle text-warning-strong border-warning-line'}
							>{humanise(b.status)}</Badge
						>
					{/if}
				{/snippet}
			</DataTable>
		</div>
	</div>

	<div>
		<h4 class="mb-2 text-sm font-semibold">Reconciliation inputs</h4>
		<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
			<DataTable
				columns={inputColumns}
				rows={inputs}
				bind:sort={inputSort}
				getKey={(row) => row.leg}
				label="Reconciliation inputs"
				empty="No reconciliation inputs."
			>
				{#snippet cell(row, column)}
					{#if column.key === 'leg'}
						{row.leg}
					{:else if column.key === 'source'}
						{row.source}
					{:else if column.key === 'api'}
						<code class="text-xs">{row.api}</code>
					{/if}
				{/snippet}
			</DataTable>
		</div>
	</div>
</div>
