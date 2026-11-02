<script lang="ts">
	import { Badge } from '../ui/badge/index.js';
	import { Money } from '../ui/money/index.js';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../ui/data-table/index.js';
	import { activityDate, activityLabel } from './activity.js';
	import type { Activity } from './types.js';

	// The activity 'table' variant: the ledger as a sortable DataTable (the 'list' variant keeps
	// the grouped ActivityList look). Credits go positive-green, debits stay plain — the same
	// reading ActivityList established.
	let {
		activity,
		/**
		 * Bound the rows and scroll them, as a Tailwind max-height class. Absent ⇒ the table is as
		 * tall as its ledger, which is right inside a page that scrolls and wrong on the portal's
		 * activity page, where the block IS the page and a long ledger ran off the bottom.
		 *
		 * Same prop, same reasoning as `holdings-full-table`: these two are the variants `sliceCap`
		 * exempts, so they are the two that can grow without limit.
		 */
		maxHeight = undefined
	}: { activity: Activity[]; maxHeight?: string } = $props();

	let sort = $state<DataTableSort | null>(null);

	// Index-keyed: nothing in an Activity row is unique on its own.
	type Row = Activity & { key: number };
	const rows = $derived(activity.map((a, i): Row => ({ ...a, key: i })));

	const columns: DataTableColumn<Row>[] = [
		{
			key: 'date',
			header: 'Date',
			sortable: true,
			sortDefault: true,
			defaultDir: 'desc',
			// Display-string dates ('14 Jun 2026') sort wrongly as text; parse for ordering and let
			// an unparseable one sink to the blanks (input already arrives date-sorted anyway).
			sortValue: (a) => {
				const t = Date.parse(a.date);
				return Number.isFinite(t) ? t : null;
			}
		},
		// Type and account shed by CONTAINER width (the table's own span, not the viewport) — a
		// narrow activity block keeps date + description + amount, the ledger's essentials. The
		// description cell takes the leftover width (`w-full max-w-0`) so long narratives
		// ellipsise instead of forcing a sideways scroll.
		{ key: 'type', header: 'Type', sortable: true, hideBelowWidth: 'lg' },
		{ key: 'description', header: 'Description', sortable: true, cellClass: 'w-full max-w-0' },
		{
			key: 'account',
			header: 'Account',
			sortable: true,
			sortValue: (a) => a.wrapper ?? null,
			hideBelowWidth: '2xl'
		},
		{
			key: 'amount',
			header: 'Amount',
			sortable: true,
			align: 'right',
			defaultDir: 'desc'
		}
	];
</script>

<DataTable
	{columns}
	{rows}
	bind:sort
	getKey={(a) => a.key}
	label="Recent activity"
	scroll={!!maxHeight}
	{maxHeight}
	empty="Payments in, dividends, trades and fees will appear here."
>
	{#snippet cell(a, column)}
		{#if column.key === 'date'}
			<span class="whitespace-nowrap tabular-nums">{activityDate(a.date)}</span>
		{:else if column.key === 'type'}
			<span class="text-muted-foreground">{activityLabel(a.type)}</span>
		{:else if column.key === 'description'}
			<span class="block min-w-0 truncate font-medium">{a.description}</span>
		{:else if column.key === 'account'}
			{#if a.wrapper}<Badge variant="outline" class="font-normal" title={a.wrapper}
					>{a.wrapper}</Badge
				>{:else}<span class="text-muted-foreground">—</span>{/if}
		{:else if column.key === 'amount'}
			<!-- BOTH directions, in the money pair (`--positive`/`--negative`) this feed already
			     uses for a credit. Only the credit was tinted, so a debit took the body colour and
			     a column of movements read as "green, green, plain, green" — the eye takes the
			     untinted one for another credit, which is the one reading a ledger must never
			     allow. Zero stays neutral — it is neither.

			     Through `tone`, NOT a class: a caller-supplied colour reaches the pounds and not
			     the PENCE, which keep `text-muted-foreground` — so a red total ended in grey
			     pence. Only the tone path fades them into the tint instead. -->
			<Money amount={a.amount} currency={a.currency} signed tone="money" class="font-medium" />
		{/if}
	{/snippet}
</DataTable>
