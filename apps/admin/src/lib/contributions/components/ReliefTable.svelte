<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	/** Both multi: an accumulating, fully-loaded book filtered client-side, so "2023/24 or
	 *  2024/25" is a real question with no round trip behind it. */
	export function reliefFilterDefs(input: {
		taxYearOptions: FilterDef['options'];
		stateOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{ key: 'taxYear', label: 'Tax year', options: input.taxYearOptions },
			{ key: 'state', label: 'Relief state', options: input.stateOptions }
		];
	}
</script>

<script lang="ts">
	import {
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		Table,
		formatGBP
	} from '@graphene/ui';
	import { ListingToolbar, matches, optionsFrom, search } from '@graphene/ui/book/listing';
	import { reliefOf } from '$lib/contributions/ras';
	import { STATUSES } from '$lib/contributions/status';
	import type { Contribution } from '$lib/contributions/types';
	import type { WorkbenchStore } from '$lib/contributions/store.svelte';

	let { wb }: { wb: WorkbenchStore } = $props();

	const minIdx = STATUSES.indexOf('ml_recorded');
	// The book this table works from: personal contributions that have reached ML. It never drops
	// a row once relief starts accruing — "in transit" through "received" all stay on screen, and
	// only the filters below narrow what's visible.
	const baseRows = $derived(
		wb.contributions.filter((c) => c.type === 'personal' && STATUSES.indexOf(c.status) >= minIdx)
	);
	const received = (c: { status: string }) =>
		c.status === 'tax_relief_received' || c.status === 'reconciled';
	const stateLabel = (s: string) =>
		s === 'tax_relief_received' || s === 'reconciled'
			? 'Received'
			: s === 'ras_received'
				? 'At ML'
				: 'In transit';

	// Search + filters, client-side like every other book listing: this is an accumulating,
	// fully-loaded book, so narrowing it is a predicate rather than a round trip.
	let query = $state('');
	let taxYearFilter = $state<string[]>([]);
	let stateFilter = $state<string[]>([]);
	const taxYearOptions = $derived(optionsFrom(baseRows, (c) => c.taxYear));
	// The relief-state filter reads through `stateLabel`, the same helper the State column
	// renders with — not the raw status — so its options are the exact three words the column
	// prints. Offering raw statuses here ("ras_applied", "ras_received"…) would let someone filter
	// for a word the table never shows.
	const stateOptions = $derived(optionsFrom(baseRows, (c) => stateLabel(c.status)));
	const dirty = $derived(taxYearFilter.length > 0 || stateFilter.length > 0);
	function clearFilters(): void {
		taxYearFilter = [];
		stateFilter = [];
	}
	const rows = $derived(
		baseRows.filter(
			(c) =>
				matches(taxYearFilter, c.taxYear) &&
				matches(stateFilter, stateLabel(c.status)) &&
				search(query, c.member, c.seiAccount, c.scheme)
		)
	);

	const filterDefs = $derived(reliefFilterDefs({ taxYearOptions, stateOptions }));
	const barValue = $derived<Record<string, string[]>>({
		...(taxYearFilter.length ? { taxYear: taxYearFilter } : {}),
		...(stateFilter.length ? { state: stateFilter } : {})
	});
	function setFilters(next: Record<string, string[]>): void {
		taxYearFilter = next.taxYear ?? [];
		stateFilter = next.state ?? [];
	}

	// Totals track the FILTERED rows: an operator who has narrowed to one tax year expects the
	// total on screen to be that year's total, not the whole book's.
	const totalExpected = $derived(rows.reduce((a, c) => a + reliefOf(c), 0));
	const totalReceived = $derived(rows.reduce((a, c) => a + (received(c) ? reliefOf(c) : 0), 0));

	// `reliefExpected`/`reliefReceived`/`state` are all derived from `reliefOf`/`stateLabel`, so
	// each carries an explicit `sortValue` reading the same computation the cell renders.
	const columns: DataTableColumn<Contribution>[] = [
		{
			key: 'member',
			header: 'Member',
			cellClass: 'font-medium',
			sortable: true,
			sortDefault: true
		},
		{ key: 'taxYear', header: 'Tax year', cellClass: 'font-mono text-xs', sortable: true },
		{
			key: 'net',
			header: 'Net cont.',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{
			key: 'reliefExpected',
			header: 'Relief expected',
			align: 'right',
			cellClass: 'text-brand-strong tabular-nums',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (c) => reliefOf(c)
		},
		{
			key: 'reliefReceived',
			header: 'Relief received',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (c) => (received(c) ? reliefOf(c) : 0)
		},
		{
			key: 'state',
			header: 'State',
			cellClass: 'text-muted-foreground',
			sortable: true,
			sortValue: (c) => stateLabel(c.status)
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let sort = $state<DataTableSort | null>(null);
</script>

<div class="space-y-3">
	<ListingToolbar
		bind:query
		placeholder="Search member, SEI account or scheme"
		{dirty}
		onclear={clearFilters}
		{filterDefs}
		filterValue={barValue}
		onfilters={setFilters}
	/>
	<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
		<DataTable
			{columns}
			{rows}
			bind:sort
			getKey={(c) => c.id}
			label="Relief"
			empty="No contributions match."
		>
			{#snippet cell(c, column)}
				{#if column.key === 'member'}
					{c.member}
				{:else if column.key === 'taxYear'}
					{c.taxYear}
				{:else if column.key === 'net'}
					{formatGBP(c.net)}
				{:else if column.key === 'reliefExpected'}
					{formatGBP(reliefOf(c))}
				{:else if column.key === 'reliefReceived'}
					{received(c) ? formatGBP(reliefOf(c)) : '—'}
				{:else if column.key === 'state'}
					{stateLabel(c.status)}
				{/if}
			{/snippet}
			{#snippet footer()}
				<!-- A summary, so it sits outside the sortable set and stays a total whatever order
				     the reader puts the rows into. In the table's own `<tfoot>`, not a second table
				     stacked beneath: only then do the figures line up under the money above them. -->
				<Table.Row class="bg-muted/40 font-semibold">
					<Table.Cell>In-transit total</Table.Cell>
					<Table.Cell></Table.Cell>
					<Table.Cell></Table.Cell>
					<Table.Cell class="text-right tabular-nums">{formatGBP(totalExpected)}</Table.Cell>
					<Table.Cell class="text-right tabular-nums">{formatGBP(totalReceived)}</Table.Cell>
					<Table.Cell>{formatGBP(totalExpected - totalReceived)} outstanding</Table.Cell>
				</Table.Row>
			{/snippet}
		</DataTable>
	</div>
</div>
