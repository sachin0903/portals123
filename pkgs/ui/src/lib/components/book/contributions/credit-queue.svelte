<script module lang="ts">
	import type { FilterDef } from '../listing/filter-panel.svelte';

	/** A pure function so it can be pinned in a test without rendering the panel, which lives
	 *  behind a popover shut at SSR. */
	export function creditQueueFilterDefs(input: {
		categoryOptions: FilterDef['options'];
	}): FilterDef[] {
		return [{ key: 'category', label: 'Suggested category', options: input.categoryOptions }];
	}
</script>

<script lang="ts">
	// Money that arrived with nothing said about it — the front of path A.
	//
	// This is the step the flow calls "adviser categorises transaction", and until now it did not
	// exist in the portal at all: `platform.transactions` had no reader on this surface, so a
	// credit sat in the feed and the only way to record it was to book a contribution as though
	// the money had not arrived yet (which then raised a SEI expectation for a payment already
	// received).
	//
	// The category is PRE-SELECTED from the custodian's own description code where it says
	// something useful — SEI stamps 21121 on a member net contribution and 21124 on an employer
	// one — but never submitted for you. The code says how the money was labelled at the
	// custodian; the HMRC decision is the firm's.
	import InfoIcon from '@lucide/svelte/icons/info';
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Badge } from '../../ui/badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import { DataTable } from '../../ui/data-table/index.js';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { formatDate } from '../../../dates.js';
	import { Money } from '../../ui/money/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import * as Select from '../../ui/select/index.js';
	import { ListingToolbar, matches, optionsFrom, search } from '../listing/index.js';
	import { CONTRIBUTION_CATEGORIES, type ContributionCategory } from './types.js';
	import type { ContributionLinks, UnclassifiedCredit } from './types.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { clipName } from '../../../text-shell.js';

	let {
		credits,
		busy = false,
		links,
		onclassify
	}: {
		credits: UnclassifiedCredit[];
		busy?: boolean;
		/** Injected navigation, as everywhere else in the workbench — this queue hardcodes no
		 *  hrefs. Absent, or without an `account` builder, the account renders as plain text. */
		links?: ContributionLinks;
		/** Absent ⇒ read-only (the admin portal's first mode): the queue still shows the work,
		 *  it just cannot be actioned. */
		onclassify?: (transactionId: number, category: ContributionCategory) => void;
	} = $props();

	let sort = $state<DataTableSort | null>({ key: 'date', dir: 'desc' });

	/** Per-row category choice, keyed by transaction id and seeded from the custodian's code. */
	let chosen = $state<Record<number, ContributionCategory>>({});
	const categoryFor = (credit: UnclassifiedCredit): ContributionCategory =>
		chosen[credit.id] ?? credit.suggested_category ?? 'personal_contribution';
	const categoryLabel = (value: ContributionCategory): string =>
		CONTRIBUTION_CATEGORIES.find((c) => c.value === value)?.label ?? value;

	// Client-side only, and deliberately so: `GET /contributions/unclassified` takes no `q` or
	// filter params (apps/api/app/routers/contributions.py), so there is no server query to hand
	// the toolbar — filtering the rows already in hand is the only honest option, not a shortcut
	// taken instead of a server one.
	let query = $state('');
	let categoryFilter = $state<string[]>([]);
	// Options come from the rows THIS queue holds, not the full category vocabulary: a suggestion
	// only a firm with an unusual custodian mix would see is not worth offering to everyone else,
	// and `optionsFrom` already drops a value (`suggested_category: null`, every relief credit)
	// that would otherwise match no row.
	const categoryOptions = $derived(
		optionsFrom(
			credits,
			(c) => c.suggested_category,
			(value) => categoryLabel(value as ContributionCategory)
		)
	);
	const filterDefs = $derived(creditQueueFilterDefs({ categoryOptions }));
	const barValue = $derived<Record<string, string[]>>(
		categoryFilter.length ? { category: categoryFilter } : {}
	);
	function applyFilters(next: Record<string, string[]>): void {
		categoryFilter = next.category ?? [];
	}
	const dirty = $derived(query.trim() !== '' || categoryFilter.length > 0);
	function clear() {
		query = '';
		categoryFilter = [];
	}
	const filteredCredits = $derived(
		credits.filter(
			(c) =>
				matches(categoryFilter, c.suggested_category) &&
				search(
					query,
					c.client_name,
					c.account_name,
					c.source_type,
					c.provider_description,
					c.source_transaction_id
				)
		)
	);
	// The router hard-caps this fetch at 1000 rows (`limit: int = Query(..., le=1000)`) and there is
	// no `total` here — the endpoint answers a bare list, not the paged envelope other listings get —
	// so this is the only fact the component can state honestly: it cannot say there are MORE than
	// 1000, only that 1000 is where the fetch itself would have stopped regardless. A firm with a
	// smaller queue than that never sees this note.
	const capped = $derived(credits.length >= 1000);

	const columns: DataTableColumn<UnclassifiedCredit>[] = [
		{
			key: 'date',
			header: 'Received',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (c) => (c.transaction_date ? new Date(c.transaction_date) : null),
			headerClass: 'w-[12%]'
		},
		{ key: 'client_name', header: 'Client', sortable: true, headerClass: 'w-[16%]' },
		{
			key: 'account_name',
			header: 'Account',
			sortable: true,
			hideBelow: 'lg',
			headerClass: 'w-[12%]'
		},
		{
			key: 'source',
			header: 'Description',
			sortable: true,
			sortValue: (c) => c.source_type ?? '',
			hideBelow: 'md',
			headerClass: 'w-[16%]'
		},
		{
			key: 'amount',
			header: 'Amount',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			headerClass: 'w-[12%]'
		},
		// `min-w-44` matches the Select.Trigger's, so the control never overlaps the next column.
		{ key: 'category', header: 'Classify as', headerClass: 'w-[22%] min-w-44' },
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-[10%] min-w-24' }
	];
</script>

<!-- The table's empty state is the FILTER MISS and nothing else: the whole component already
     branches on `credits.length === 0` below, so the DataTable exists only when there are rows
     and `filteredCredits` can be empty only when something is narrowing. A "nothing to classify"
     branch here would be dead code claiming to be a state — it was written, and it could never
     have rendered. What the table owed the reader is the Clear that undoes the narrowing, rather
     than a bare sentence and a toolbar to go and find. -->
{#snippet noCreditMatches()}
	<EmptyState
		icon={SearchXIcon}
		title="No credits match your filters"
		description="Try a different suggested category or search term, or clear the filters to see all {credits.length} waiting."
		action={clearCredits}
		compact
		chromeless
	/>
{/snippet}

{#snippet clearCredits()}
	<Button variant="outline" size="sm" onclick={clear}>Clear filters</Button>
{/snippet}

{#if credits.length === 0}
	<Alert>
		<AlertDescription>
			Nothing unclassified. Every credit that has reached the book has a contribution or a transfer
			against it.
		</AlertDescription>
	</Alert>
{:else}
	<div class="space-y-3">
		<p class="text-muted-foreground text-sm">
			{credits.length} credit{credits.length === 1 ? '' : 's'} arrived with no record against {credits.length ===
			1
				? 'it'
				: 'them'}. Classifying one creates the contribution against the money that actually landed —
			no payment expectation is raised, because the payment already happened.
			{#if capped}
				Showing the first {credits.length.toLocaleString('en-GB')} — narrow the search or category filter
				below to reach a specific one if your queue runs longer than that.
			{/if}
		</p>

		<ListingToolbar
			bind:query
			placeholder="Search client, account, description or reference"
			{dirty}
			onclear={clear}
			{filterDefs}
			filterValue={barValue}
			onfilters={applyFilters}
		/>

		<DataTable
			{columns}
			rows={filteredCredits}
			bind:sort
			getKey={(c) => c.id}
			fixed
			empty={noCreditMatches}
		>
			{#snippet cell(credit, column)}
				{#if column.key === 'date'}
					<span class="whitespace-nowrap tabular-nums">{formatDate(credit.transaction_date)}</span>
				{:else if column.key === 'client_name'}
					{@const client = clipName(credit.client_name ?? 'Unknown client')}
					<span class="block min-w-0">
						<TruncatedText
							text={client.text}
							full={client.full}
							class="w-fit max-w-full font-medium"
						/>
						<RecordId value={`${credit.source_system} · ${credit.source_transaction_id}`} />
					</span>
				{:else if column.key === 'account_name'}
					<!-- "Which account did this land in?" is the question that decides whether a credit
					     is even this client's to classify, and answering it used to mean leaving the
					     queue. Guarded on both the builder and the uid, so a mount with no account
					     route keeps the plain name rather than a dead link. -->
					{@const account = clipName(credit.account_name ?? credit.account_uid ?? '—')}
					<TruncatedText
						text={account.text}
						full={account.full}
						href={links?.account && credit.account_uid
							? links.account(credit.account_uid)
							: undefined}
						class="text-muted-foreground w-fit max-w-full"
					/>
				{:else if column.key === 'source'}
					<span class="block min-w-0">
						<span class="block truncate">{credit.source_type ?? '—'}</span>
						{#if credit.provider_description}
							<!-- The custodian's own label for its description code. Shown because it is the
							     provenance for the pre-selected category — an adviser accepting a suggestion
							     should be able to see what it was based on. -->
							<span class="text-muted-foreground block truncate text-xs">
								{credit.provider_description}
							</span>
						{/if}
					</span>
				{:else if column.key === 'amount'}
					<Money amount={credit.amount} class="font-medium" />
				{:else if column.key === 'category'}
					{#if credit.is_relief_payment}
						<!-- Not classifiable. This is the basic-rate relief the scheme reclaimed arriving
						     back from SEI; recording it as a fresh contribution would double-count the
						     member's annual allowance and reclaim relief on relief. It shows here so the
						     credit is accounted for, not so it can be actioned. -->
						<Badge variant="outline" class="border-success-line text-success-strong">
							Tax relief credit
						</Badge>
					{:else}
						<Select.Root
							type="single"
							value={categoryFor(credit)}
							onValueChange={(value) => (chosen[credit.id] = value as ContributionCategory)}
							disabled={busy || !onclassify}
						>
							<Select.Trigger class="w-full min-w-44" aria-label="Classify this credit">
								{categoryLabel(categoryFor(credit))}
							</Select.Trigger>
							<Select.Content>
								{#each CONTRIBUTION_CATEGORIES as option (option.value)}
									<Select.Item value={option.value} label={option.label} />
								{/each}
							</Select.Content>
						</Select.Root>
					{/if}
				{:else if column.key === 'actions'}
					{#if onclassify && !credit.is_relief_payment}
						<Button
							size="sm"
							disabled={busy}
							onclick={() => onclassify?.(credit.id, categoryFor(credit))}
						>
							Classify
						</Button>
					{/if}
				{/if}
			{/snippet}
		</DataTable>

		{#if filteredCredits.some((c) => c.is_relief_payment)}
			<p class="text-muted-foreground flex items-start gap-2 text-xs">
				<InfoIcon class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
				<span>
					Tax relief credits are shown for completeness and cannot be classified — they are the
					basic-rate relief already reclaimed against a contribution in this book.
				</span>
			</p>
		{/if}
	</div>
{/if}
