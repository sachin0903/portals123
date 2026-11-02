<script lang="ts">
	import { Badge } from '../ui/badge/index.js';
	import { Money } from '../ui/money/index.js';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../ui/data-table/index.js';
	import { accountStatusChip } from './account-status.js';
	import { changeDirection } from './periods.js';
	import { formatPct, formatPctPlain, sanePct } from '../../money.js';
	import { cn } from '../../utils.js';
	import type { Wrapper } from './types.js';
	import { LINK_RECORD } from '../../text-shell.js';

	// The accounts 'table' variant: the whole book as a sortable listing — a power-user look
	// that's only ever chosen explicitly (resolveVariant never auto-picks it).
	let {
		wrappers,
		/** False when `wrappers` is one page of a paginated book — client-side sort would lie. */
		sortable = true
	}: { wrappers: Wrapper[]; sortable?: boolean } = $props();

	let sort = $state<DataTableSort | null>(null);

	// Index-keyed rows: (name, owner, type) isn't guaranteed unique in a real book, and a keyed
	// each with duplicate keys throws. The index is stable — DataTable sorts a copy for display.
	type Row = Wrapper & { key: number };
	const rows = $derived(wrappers.map((w, i): Row => ({ ...w, key: i })));

	// Columns collapse by CONTAINER width (the table's own span), not viewport — a w=4 block on a
	// desktop screen is ~350px and must shed columns like a phone would. Name + Value always show.
	// The name cell takes the leftover width (`w-full max-w-0`): on an auto-layout table that is
	// what actually lets `truncate` fire instead of the long name forcing a horizontal scroll.
	const columns: DataTableColumn<Row>[] = [
		{
			key: 'name',
			header: 'Name',
			sortable: true,
			sortDefault: true,
			sortValue: (w) => w.name,
			cellClass: 'w-full max-w-0'
		},
		{ key: 'type', header: 'Type', sortable: true, hideBelowWidth: 'md' },
		{
			key: 'value',
			header: 'Value',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (w) => w.value
		},
		{
			key: 'return',
			header: 'Return',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (w) => w.gainPct,
			hideBelowWidth: 'lg'
		}
	];
</script>

<DataTable
	{columns}
	{rows}
	bind:sort
	getKey={(w) => w.key}
	label="Accounts"
	sortDisabled={!sortable}
	empty="Your accounts will appear here once they are opened."
>
	{#snippet cell(w, column)}
		{#if column.key === 'name'}
			{@const chip = accountStatusChip(w.status)}
			<a href={w.href ?? `/accounts/${w.type}`} class="block min-w-0 {LINK_RECORD}">
				<span class="flex min-w-0 items-center gap-2">
					<span class="min-w-0 truncate font-medium">{w.name}</span>
					{#if chip}
						<!-- Legacy /accounts parity: a NON-active account wears its status. Active
						     accounts stay chip-less — a book-wide wall of green is noise. -->
						<StatusBadge tone={chip.tone} class="shrink-0">{chip.label}</StatusBadge>
					{/if}
				</span>
				<span class="text-muted-foreground block truncate text-xs">
					{w.owner}{#if w.delegated}&nbsp;· Delegated{/if}
				</span>
			</a>
		{:else if column.key === 'type'}
			<Badge variant="outline" class="font-normal">{w.type}</Badge>
		{:else if column.key === 'value'}
			<Money amount={w.value} class="font-medium" />
		{:else if column.key === 'return'}
			<!-- Neutral-zero + sanePct, unified with the stack and cards variants: a zero-change
			     return rendered green "+0.0%" claims a gain that never happened, and an implausible
			     figure (junk cost basis) is worse than no figure. -->
			{@const gainPct = sanePct(w.gainPct)}
			{@const dir = gainPct === null ? null : changeDirection(gainPct)}
			<span
				class={cn(
					'tabular-nums',
					dir === 'up' && 'text-positive',
					dir === 'down' && 'text-negative',
					(dir === 'flat' || dir === null) && 'text-muted-foreground'
				)}
			>
				{dir === null ? '—' : dir === 'flat' ? formatPctPlain(0) : formatPct(gainPct ?? 0)}
			</span>
		{/if}
	{/snippet}
</DataTable>
