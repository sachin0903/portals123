<script module lang="ts">
	import type { FilterDef } from '../listing/filter-panel.svelte';

	/** A pure function so it can be pinned in a test without rendering the panel, which lives
	 *  behind a popover shut at SSR. */
	export function reliefMonitorFilterDefs(input: {
		statusOptions: FilterDef['options'];
		taxYearOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{ key: 'status', label: 'Relief status', options: input.statusOptions },
			{ key: 'tax_year', label: 'Tax year', options: input.taxYearOptions }
		];
	}
</script>

<script lang="ts">
	// Relief at source: expected vs received, per member.
	//
	// The prototype's "Relief at Source" tab, built on the real ledger rather than invented
	// batches. What is deliberately NOT here is the monthly reclaim-batch board: SEI pays relief
	// as a batch against the scheme, and we hold no batch identifier — so a batch table would be
	// rows we made up. What we can say honestly is per-member: this much relief is due on these
	// contributions, this much has been credited back.
	//
	// `received` is at ACCOUNT grain for the same reason (`app/matching.py::relief_credits`):
	// splitting a batch credit across the contributions that earned it is an allocation, not a
	// fact, so the monitor shows the account position and leaves the split alone.
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import { StatusBadge, type StatusTone } from '../../ui/status-badge/index.js';
	import { DataTable } from '../../ui/data-table/index.js';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { Money } from '../../ui/money/index.js';
	import { formatGBP } from '../../../money.js';
	import { ListingToolbar, matches, optionsFrom, search } from '../listing/index.js';
	import type { Contribution, ContributionLinks } from './types.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { clipName } from '../../../text-shell.js';

	let {
		rows,
		links
	}: {
		rows: Contribution[];
		/** Injected navigation, per the portal waterfall — this monitor hardcodes no hrefs. Absent,
		 *  or without an `account` builder, the account line under each member is plain text. */
		links?: ContributionLinks;
	} = $props();

	/** Only contributions that earn relief. Employer contributions are paid gross — the employer
	 *  takes the corporation-tax deduction — so listing them as "relief awaited" would leave a
	 *  permanently outstanding row nobody can clear. */
	const claimable = $derived(rows.filter((c) => c.relief?.status !== 'not_applicable'));

	const outstanding = $derived(
		claimable
			.filter((c) => c.relief.status === 'awaited' || c.relief.status === 'short')
			.reduce((total, c) => total + c.relief.outstanding, 0)
	);
	const expected = $derived(claimable.reduce((total, c) => total + c.relief.expected, 0));

	let sort = $state<DataTableSort | null>({ key: 'outstanding', dir: 'desc' });

	// Client-side, over the whole firm-wide claimable set: there is no server query behind this
	// monitor (it is handed a `Contribution[]` prop, not a listing page), so narrowing here is
	// filtering the rows already in hand rather than a stand-in for a query the API could answer.
	let query = $state('');
	let statusFilter = $state<string[]>([]);
	let taxYearFilter = $state<string[]>([]);
	const dirty = $derived(
		query.trim() !== '' || statusFilter.length > 0 || taxYearFilter.length > 0
	);
	function clear() {
		query = '';
		statusFilter = [];
		taxYearFilter = [];
	}

	const TONE: Record<string, StatusTone> = {
		awaited: 'warning',
		short: 'danger',
		received: 'success'
	};
	const LABEL: Record<string, string> = {
		awaited: 'Awaited',
		short: 'Short',
		received: 'Received'
	};

	// Options are read off `claimable`, not the full status/tax-year vocabulary: `claimable` has
	// already dropped `not_applicable` (employer contributions earn no relief), so that value can
	// never appear as something to filter by here.
	const statusOptions = $derived(
		optionsFrom(
			claimable,
			(c) => c.relief.status,
			(value) => LABEL[value] ?? value
		)
	);
	const taxYearOptions = $derived(optionsFrom(claimable, (c) => c.tax_year));
	const filterDefs = $derived(reliefMonitorFilterDefs({ statusOptions, taxYearOptions }));
	const barValue = $derived<Record<string, string[]>>({
		...(statusFilter.length ? { status: statusFilter } : {}),
		...(taxYearFilter.length ? { tax_year: taxYearFilter } : {})
	});
	function applyFilters(next: Record<string, string[]>): void {
		statusFilter = next.status ?? [];
		taxYearFilter = next.tax_year ?? [];
	}
	const filteredRows = $derived(
		claimable.filter(
			(c) =>
				matches(statusFilter, c.relief.status) &&
				matches(taxYearFilter, c.tax_year) &&
				search(query, c.client_name, c.account_name)
		)
	);

	const fig = {
		sortable: true,
		align: 'right',
		defaultDir: 'desc',
		headerClass: 'w-[14%]'
	} as const;
	const columns: DataTableColumn<Contribution>[] = [
		{ key: 'client_name', header: 'Member', sortable: true, headerClass: 'w-[20%]' },
		{
			key: 'tax_year',
			header: 'Tax year',
			sortable: true,
			hideBelow: 'md',
			headerClass: 'w-[12%]'
		},
		{ ...fig, key: 'amount', header: 'Net paid' },
		{ ...fig, key: 'expected', header: 'Relief due', sortValue: (c) => c.relief.expected },
		{
			...fig,
			key: 'received',
			header: 'Credited',
			hideBelow: 'lg',
			sortValue: (c) => c.relief.received
		},
		{ ...fig, key: 'outstanding', header: 'Outstanding', sortValue: (c) => c.relief.outstanding },
		{ key: 'status', header: 'Status', sortable: true, headerClass: 'w-[12%]' }
	];
</script>

<!-- As on the credit queue: the table's empty state is the filter miss alone. `claimable.length
     === 0` is handled by the whole component below, so a "nothing to track" branch here could
     never render. -->
{#snippet noReliefMatches()}
	<EmptyState
		icon={SearchXIcon}
		title="No contributions match your filters"
		description="Try a different status or tax year, or clear the filters to see all {claimable.length} being tracked."
		action={clearRelief}
		compact
		chromeless
	/>
{/snippet}

{#snippet clearRelief()}
	<Button variant="outline" size="sm" onclick={clear}>Clear filters</Button>
{/snippet}

{#if claimable.length === 0}
	<Alert>
		<AlertDescription>
			No contributions in this book earn relief at source. Employer contributions are paid gross, so
			there is nothing for the scheme to reclaim.
		</AlertDescription>
	</Alert>
{:else}
	<div class="space-y-3">
		<p class="text-muted-foreground text-sm">
			{formatGBP(expected)} of basic-rate relief due across {claimable.length} contribution{claimable.length ===
			1
				? ''
				: 's'}{#if outstanding > 0}, <span class="text-warning-strong"
					>{formatGBP(outstanding)} not yet credited</span
				>{/if}.
		</p>

		<ListingToolbar
			bind:query
			placeholder="Search member or account"
			{dirty}
			onclear={clear}
			{filterDefs}
			filterValue={barValue}
			onfilters={applyFilters}
		/>

		<DataTable
			{columns}
			rows={filteredRows}
			bind:sort
			getKey={(c) => c.uid}
			fixed
			empty={noReliefMatches}
		>
			{#snippet cell(contribution, column)}
				{#if column.key === 'client_name'}
					{@const client = clipName(contribution.client_name ?? '—')}
					{@const account = clipName(contribution.account_name ?? contribution.account_uid ?? '')}
					<span class="block min-w-0">
						<TruncatedText
							text={client.text}
							full={client.full}
							class="w-fit max-w-full font-medium"
						/>
						<!-- Relief is credited at ACCOUNT grain (see the note at the foot), so the
						     account is the record a short or awaited row is actually chased against —
						     which is why this line links where the member's name, having no route
						     injected here, does not. -->
						<TruncatedText
							text={account.text}
							full={account.full}
							href={links?.account && contribution.account_uid
								? links.account(contribution.account_uid)
								: undefined}
							class="text-muted-foreground w-fit max-w-full text-xs"
						/>
					</span>
				{:else if column.key === 'tax_year'}
					<span class="text-muted-foreground whitespace-nowrap">{contribution.tax_year ?? '—'}</span
					>
				{:else if column.key === 'amount'}
					<Money amount={contribution.amount} />
				{:else if column.key === 'expected'}
					<Money amount={contribution.relief.expected} class="font-medium" />
				{:else if column.key === 'received'}
					{#if contribution.relief.received === null}
						<span class="text-muted-foreground">—</span>
					{:else}
						<Money amount={contribution.relief.received} />
					{/if}
				{:else if column.key === 'outstanding'}
					{#if contribution.relief.outstanding === 0}
						<span class="text-muted-foreground">—</span>
					{:else}
						<Money amount={contribution.relief.outstanding} class="text-warning-strong" />
					{/if}
				{:else if column.key === 'status'}
					<StatusBadge tone={TONE[contribution.relief.status] ?? 'neutral'}>
						{LABEL[contribution.relief.status] ?? contribution.relief.status}
					</StatusBadge>
				{/if}
			{/snippet}
		</DataTable>

		<p class="text-muted-foreground text-xs">
			Credited amounts are the basic-rate relief SEI has paid against the member's account
			(description codes 21122 / 21126). SEI reclaims in a monthly batch against the scheme rather
			than per contribution, so the credited figure is the account's position — it is not split
			across the contributions that earned it.
		</p>
	</div>
{/if}
