<script lang="ts">
	import {
		Badge,
		Button,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		formatGBP
	} from '@graphene/ui';
	import {
		type FilterDef,
		ListingToolbar,
		humanise,
		matches,
		optionsFrom,
		search
	} from '@graphene/ui/book/listing';
	import { BREAK_STATUS_LABEL, BREAK_TYPE_LABEL, ROUTE_LABEL } from '$lib/transfers/status';
	import type { TransfersStore } from '$lib/transfers/store.svelte';
	import type { BreakStatus, BreakType, Transfer, TransferRoute } from '$lib/transfers/types';
	import RouteBadge from './RouteBadge.svelte';

	let { wb }: { wb: TransfersStore } = $props();

	const tone: Record<string, string> = {
		open: 'bg-destructive-subtle text-destructive-strong border-destructive-line',
		investigating: 'bg-warning-subtle text-warning-strong border-warning-line',
		resolved: 'bg-success-subtle text-success-strong border-success-line',
		accepted: 'bg-muted text-muted-foreground border-border'
	};
	const custodyOf = (legs: { value: number }[], expected: number) => legs.at(-1)?.value ?? expected;
	// Both figures the table sorts and renders on: `custody` and `Δ` are computed, not stored, so
	// the sort and the cell must read the same derivation or the visible order would disagree with
	// what an operator sees printed.
	const expectedOf = (t: Transfer) => t.recon?.expected ?? t.value;
	const custodyValueOf = (t: Transfer) => {
		const expected = expectedOf(t);
		return t.recon ? custodyOf(t.recon.legs, expected) : expected;
	};

	// Options are built from `wb.breaks` itself, not the whole transfers book — this table is a
	// dedicated register of open breaks across every firm, so a filter should only ever offer a
	// route, break type or status that a break is actually sitting against.
	let query = $state('');
	/** The filter bar's state. ONE object: the bar hands back the whole selection, and three
	 *  separate strings could not express "Cash OR Stock" anyway. */
	let barValue = $state<Record<string, string[]>>({});
	const pick = (key: string) => barValue[key] ?? [];
	const routeOptions = $derived(
		optionsFrom(
			wb.breaks,
			(t) => t.route,
			(r) => ROUTE_LABEL[r as TransferRoute]
		)
	);
	// Labelled from the named maps, not `humanise`: ML is Morgan Lloyd, and the column below reads
	// from the same maps so a filter and a row can never call one break two different things.
	const breakTypeOptions = $derived(
		optionsFrom(
			wb.breaks,
			(t) => t.breakType,
			(v) => BREAK_TYPE_LABEL[v as BreakType] ?? humanise(v)
		)
	);
	const breakStatusOptions = $derived(
		optionsFrom(
			wb.breaks,
			(t) => t.breakStatus,
			(v) => BREAK_STATUS_LABEL[v as BreakStatus] ?? humanise(v)
		)
	);
	const dirty = $derived(Object.keys(barValue).length > 0);
	function clearFilters(): void {
		barValue = {};
	}

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'route', label: 'Route', options: routeOptions },
		{ key: 'breakType', label: 'Break type', options: breakTypeOptions },
		{ key: 'breakStatus', label: 'Break status', options: breakStatusOptions }
	]);
	const rows = $derived(
		wb.breaks.filter(
			(t) =>
				matches(pick('route'), t.route) &&
				matches(pick('breakType'), t.breakType) &&
				matches(pick('breakStatus'), t.breakStatus) &&
				search(query, t.client, t.id, t.provider, t.firm)
		)
	);

	// `custody`/`Δ` sort on the same derivation the cells render, never the formatted string;
	// `status`/`breakType` sort on the label they show, since that's the mapped word the map may
	// substitute for the raw enum. The trailing actions column carries no order of its own.
	const columns: DataTableColumn<Transfer>[] = [
		{
			key: 'transfer',
			header: 'Transfer',
			sortable: true,
			sortDefault: true,
			sortValue: (t) => t.client
		},
		{
			key: 'breakType',
			header: 'Break type',
			sortable: true,
			cellClass: 'text-sm',
			sortValue: (t) =>
				t.breakType ? (BREAK_TYPE_LABEL[t.breakType] ?? humanise(t.breakType)) : ''
		},
		{
			key: 'breakStatus',
			header: 'Status',
			sortable: true,
			sortValue: (t) => BREAK_STATUS_LABEL[t.breakStatus ?? 'open']
		},
		{
			key: 'expected',
			header: 'Expected',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (t) => expectedOf(t)
		},
		{
			key: 'custody',
			header: 'Custody',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (t) => custodyValueOf(t)
		},
		{
			key: 'delta',
			header: 'Δ',
			align: 'right',
			cellClass: 'text-destructive-strong font-mono tabular-nums',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (t) => Math.abs(expectedOf(t) - custodyValueOf(t))
		},
		{ key: 'actions', header: '', align: 'right' }
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let sort = $state<DataTableSort | null>(null);
</script>

{#if wb.breaks.length === 0}
	<div class="border-border/70 bg-card text-muted-foreground rounded-xl border p-10 text-center">
		No reconciliation breaks. 🎉
	</div>
{:else}
	<div class="space-y-3">
		<ListingToolbar
			bind:query
			placeholder="Search client, transfer id, provider or firm"
			{dirty}
			onclear={clearFilters}
			{filterDefs}
			filterValue={barValue}
			onfilters={(next) => (barValue = next)}
		></ListingToolbar>
		<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
			<DataTable
				{columns}
				{rows}
				bind:sort
				getKey={(t) => t.id}
				label="Reconciliation breaks"
				empty="No breaks match."
			>
				{#snippet cell(t, column)}
					{@const expected = expectedOf(t)}
					{@const custody = custodyValueOf(t)}
					{#if column.key === 'transfer'}
						<div class="flex items-center gap-2">
							<span class="font-mono text-[11px]">{t.id}</span>
							<span class="font-medium">{t.client}</span>
							<RouteBadge route={t.route} />
						</div>
					{:else if column.key === 'breakType'}
						{t.breakType ? (BREAK_TYPE_LABEL[t.breakType] ?? humanise(t.breakType)) : '—'}
					{:else if column.key === 'breakStatus'}
						<Badge variant="outline" class={tone[t.breakStatus ?? 'open']}
							>{BREAK_STATUS_LABEL[t.breakStatus ?? 'open']}</Badge
						>
					{:else if column.key === 'expected'}
						{formatGBP(expected)}
					{:else if column.key === 'custody'}
						{formatGBP(custody)}
					{:else if column.key === 'delta'}
						−{formatGBP(Math.abs(expected - custody))}
					{:else if column.key === 'actions'}
						<Button
							size="sm"
							variant="outline"
							onclick={() => {
								wb.select(t.id);
								wb.tab = 'workbench';
							}}>Open</Button
						>
					{/if}
				{/snippet}
			</DataTable>
		</div>
	</div>
{/if}
