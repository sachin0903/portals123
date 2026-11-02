<script lang="ts">
	import {
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		Segmented,
		formatGBP
	} from '@graphene/ui';
	import { ALL, ListingToolbar, matches, search } from '@graphene/ui/book/listing';
	import type { WorkbenchStore } from '$lib/contributions/store.svelte';
	import type { SeiTransaction } from '$lib/contributions/types';

	let { wb }: { wb: WorkbenchStore } = $props();

	const memberFor = (id: number | null) =>
		id == null ? null : (wb.contributions.find((c) => c.id === id)?.member ?? `#${id}`);

	// A SEI credit has no raw status column — "matched"/"unmatched" is a PSEUDO-FIELD, computed
	// from whether `matchedContributionId` is set, not read off the row. The two values are fixed
	// and both always meaningful (an all-matched or all-unmatched pool is still filterable either
	// way), so they're hand-written here rather than built with `optionsFrom` off the rows on screen.
	const matchStateOf = (matchedContributionId: number | null) =>
		matchedContributionId !== null ? 'matched' : 'unmatched';
	// A closed two-value field reads as a switch (Segmented), not a dropdown that hides the second
	// option behind a click. `ALL` stays the first segment so "no filter" is still reachable.
	const MATCH_STATE_OPTIONS = [
		{ value: ALL, label: 'All' },
		{ value: 'matched', label: 'Matched' },
		{ value: 'unmatched', label: 'Unmatched' }
	];

	let query = $state('');
	let matchFilter = $state(ALL);
	const dirty = $derived(matchFilter !== ALL);
	function clearFilters(): void {
		matchFilter = ALL;
	}

	// This pool is unbounded — matched rows stay forever — so narrowing it client-side is a
	// predicate over what's already loaded, same as every other book listing.
	const rows = $derived(
		wb.seiPool.filter(
			(tx) =>
				matches(matchFilter, matchStateOf(tx.matchedContributionId)) &&
				search(query, tx.id, tx.seiAccount, tx.reference)
		)
	);

	const MONTHS = [
		'Jan',
		'Feb',
		'Mar',
		'Apr',
		'May',
		'Jun',
		'Jul',
		'Aug',
		'Sep',
		'Oct',
		'Nov',
		'Dec'
	];
	// The mock feed carries a display-only "24 Jun" date with no year — sorting the raw string
	// would put "5 Jan" after "24 Jun", so this is the closest thing to a real timestamp available
	// to sort chronologically rather than alphabetically.
	function parseShortDate(d: string): number {
		const [day, mon] = d.split(' ');
		const month = MONTHS.indexOf(mon);
		return month === -1 ? 0 : new Date(new Date().getFullYear(), month, Number(day)).getTime();
	}

	// `matched` is a store lookup, not a field, so it sorts on the member name it renders — an
	// unmatched row (empty string) always sorts last.
	const columns: DataTableColumn<SeiTransaction>[] = [
		{ key: 'id', header: 'Txn', cellClass: 'font-mono text-xs', sortable: true, sortDefault: true },
		{ key: 'seiAccount', header: 'SEI account', cellClass: 'font-mono text-xs', sortable: true },
		{
			key: 'amount',
			header: 'Amount',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{
			key: 'valueDate',
			header: 'Value date',
			cellClass: 'text-muted-foreground whitespace-nowrap',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (tx) => parseShortDate(tx.valueDate)
		},
		{ key: 'reference', header: 'Reference', cellClass: 'text-muted-foreground', sortable: true },
		{
			key: 'matched',
			header: 'Matched to',
			sortable: true,
			sortValue: (tx) =>
				tx.matchedContributionId != null ? (memberFor(tx.matchedContributionId) ?? '') : ''
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let sort = $state<DataTableSort | null>(null);
</script>

<div class="space-y-3">
	<p class="text-muted-foreground text-sm">
		Incoming SEI credits awaiting allocation. Matching is <strong
			>automatic on categorisation</strong
		>
		(exact SEI account + amount); a categorised contribution with no matching credit (or a credit with
		no contribution) stays unallocated here.
	</p>
	<ListingToolbar
		bind:query
		placeholder="Search transaction, SEI account or reference"
		{dirty}
		onclear={clearFilters}
	>
		{#snippet filters()}
			<Segmented
				bind:value={matchFilter}
				options={MATCH_STATE_OPTIONS}
				size="sm"
				label="Filter by match state"
			/>
		{/snippet}
	</ListingToolbar>
	<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
		<DataTable
			{columns}
			{rows}
			bind:sort
			getKey={(tx) => tx.id}
			label="SEI credit pool"
			empty="No transactions match."
		>
			{#snippet cell(tx, column)}
				{#if column.key === 'id'}
					{tx.id}
				{:else if column.key === 'seiAccount'}
					{tx.seiAccount}
				{:else if column.key === 'amount'}
					{formatGBP(tx.amount)}
				{:else if column.key === 'valueDate'}
					{tx.valueDate}
				{:else if column.key === 'reference'}
					{tx.reference}
				{:else if column.key === 'matched'}
					{#if tx.matchedContributionId != null}
						<span class="text-success-strong font-medium"
							>{memberFor(tx.matchedContributionId)}</span
						>
					{:else}
						<span class="text-muted-foreground">Unmatched</span>
					{/if}
				{/if}
			{/snippet}
		</DataTable>
	</div>
</div>
