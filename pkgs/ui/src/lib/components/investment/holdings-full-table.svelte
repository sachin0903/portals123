<script lang="ts">
	import { Badge } from '../ui/badge/index.js';
	import { CurrencyBadge } from '../ui/currency-badge/index.js';
	import { Money } from '../ui/money/index.js';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../ui/data-table/index.js';
	import { formatPct, gainTone, PCT_DIGITS } from '../../money.js';
	import { cn } from '../../utils.js';
	import LookThroughButton from './look-through-button.svelte';
	import type { Holding } from './types.js';

	// The holdings 'table' variant: the full position listing as a sortable DataTable (the 'top'
	// variant keeps the capped HoldingsTable look). No slice cap — portal-page exempts the table
	// variant, and a paginated slice gets its Pagination rendered by portal-page beneath this.
	let {
		holdings,
		/** False when `holdings` is one page of a paginated book — client-side sort would lie. */
		sortable = true,
		/**
		 * Bound the rows and scroll them, as a Tailwind max-height class. Absent ⇒ the table is as
		 * tall as its book, which is right inside a page that scrolls and wrong on the portal's
		 * holdings page, where the block IS the page and a long book ran off the bottom.
		 */
		maxHeight = undefined
	}: { holdings: Holding[]; sortable?: boolean; maxHeight?: string } = $props();

	let sort = $state<DataTableSort | null>(null);

	// Index-keyed: (name, wrapper) can repeat in a real book (the same fund bought twice into one
	// account), and duplicate keys throw in a keyed each.
	type Row = Holding & { key: number };
	const rows = $derived(holdings.map((h, i): Row => ({ ...h, key: i })));

	// Columns shed by CONTAINER width (the table's own span), in reverse priority order: name and
	// value survive any span; ticker is the first luxury. The name cell takes the leftover width
	// (`w-full max-w-0`) so 'Vanguard FTSE Developed World ex-UK Equity Index Fund' ellipsises
	// instead of forcing the whole table sideways.
	const columns: DataTableColumn<Row>[] = [
		{ key: 'name', header: 'Name', sortable: true, sortDefault: true, cellClass: 'w-full max-w-0' },
		{ key: 'ticker', header: 'Ticker', sortable: true, hideBelowWidth: '4xl' },
		{
			key: 'account',
			header: 'Account',
			sortable: true,
			sortValue: (h) => h.wrapper,
			hideBelowWidth: 'md'
		},
		{
			key: 'units',
			header: 'Units',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelowWidth: '2xl'
		},
		{
			key: 'currency',
			header: 'Currency',
			sortable: true,
			sortValue: (h) => h.currency ?? '',
			hideBelowWidth: 'lg'
		},
		{
			key: 'price',
			header: 'Price',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelowWidth: '3xl'
		},
		{
			key: 'value',
			header: 'Value',
			sortable: true,
			align: 'right',
			defaultDir: 'desc'
		},
		{
			key: 'return',
			header: 'Return',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (h) => h.gainPct,
			hideBelowWidth: 'lg'
		},
		{ key: 'lookthroughHref', header: '', align: 'right' }
	];
</script>

<DataTable
	{columns}
	{rows}
	bind:sort
	getKey={(h) => h.key}
	label="Holdings"
	sortDisabled={!sortable}
	scroll={!!maxHeight}
	{maxHeight}
	empty="Your funds and positions will appear here."
>
	{#snippet cell(h, column)}
		{#if column.key === 'name'}
			<span class="block min-w-0 truncate font-medium">{h.name}</span>
		{:else if column.key === 'ticker'}
			<span class="text-muted-foreground text-xs">{h.ticker ?? '—'}</span>
		{:else if column.key === 'account'}
			<Badge variant="outline" class="font-normal">{h.wrapper}</Badge>
		{:else if column.key === 'units'}
			{#if h.cash}
				<span class="text-muted-foreground">—</span>
			{:else}
				<span class="tabular-nums">{h.units.toLocaleString('en-GB')}</span>
			{/if}
		{:else if column.key === 'currency'}
			{#if h.currency}
				<CurrencyBadge code={h.currency} />
			{:else}
				<span class="text-muted-foreground">—</span>
			{/if}
		{:else if column.key === 'price'}
			{#if h.cash}
				<span class="text-muted-foreground">—</span>
			{:else}
				<Money amount={h.price} currency={h.currency || 'GBP'} />
			{/if}
		{:else if column.key === 'value'}
			<Money amount={h.value} currency={h.currency || 'GBP'} class="font-medium" />
		{:else if column.key === 'return'}
			<!-- "—", never "+0.0%": no cost basis is not a flat year (types.ts, `gainPct`). -->
			<span class={cn('tabular-nums', gainTone(h.gainPct, { digits: PCT_DIGITS }))}>
				{h.gainPct == null ? '—' : formatPct(h.gainPct)}
			</span>
		{:else if column.key === 'lookthroughHref' && h.lookthroughHref}
			<LookThroughButton href={h.lookthroughHref} />
		{/if}
	{/snippet}
</DataTable>
