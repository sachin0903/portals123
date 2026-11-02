<script lang="ts">
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import type { Snippet } from 'svelte';
	// The queue half of the workbench. Uses the shared DataTable + listing toolbar rather than
	// the admin prototype's hand-rolled table and filter buttons: sort affordances, density and
	// the empty state then behave the same here as on Clients, Accounts and Transactions.
	import {
		ALL,
		barValueFrom,
		type FilterDef,
		withBarValue,
		FilterSelect,
		humanise,
		ListingToolbar,
		matches,
		mergeOptions,
		optionsFrom,
		optionsFromFacet,
		isFiltered,
		search,
		type ListingQuery
	} from '../listing/index';
	import {
		CONTRIBUTION_CATEGORIES,
		CREATABLE_CONTRIBUTION_CATEGORIES,
		type Contribution,
		type ContributionLinks
	} from './types';
	import { STAGES, stageKeyOf } from './status';
	import { detectedOnPage, isDetected } from './detected';
	import StatusBadge from './status-badge.svelte';
	import { Badge } from '../../ui/badge/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import {
		DataTable,
		defaultSort,
		sortRows,
		visibleWhenHidden
	} from '../../ui/data-table/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { Money } from '../../ui/money/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import { formatDate } from '../../../dates.js';

	let {
		rows,
		selected = $bindable(null),
		listing = null,
		facets,
		onquery,
		showClient = true,
		links,
		/** The pager, rendered INSIDE this card at the foot of the list. It used to sit outside, as a
		 *  sibling of the card — which made the queue column shorter than the classify panel beside it
		 *  by exactly the pager's height, so the two never lined up. It is also where every other
		 *  listing puts it. */
		pager
	}: {
		rows: Contribution[];
		/** SERVER-DRIVEN when supplied: the API applied the search and filters, so this row of
		 *  controls describes the query rather than narrowing the rows locally. */
		listing?: ListingQuery | null;
		facets?: Record<string, string[]>;
		onquery?: (next: ListingQuery) => void;
		/** uid of the selected row — drives the classify panel. */
		selected?: string | null;
		showClient?: boolean;
		/** Injected navigation. Absent ⇒ the client's and the account's names are plain text, never a
		 *  dead link. `account` is optional within it, so a mount with a client route and no account
		 *  route links the one it can address. */
		links?: ContributionLinks;
		pager?: Snippet;
	} = $props();

	let query = $state('');
	let statusFilter = $state(ALL);
	let categoryFilter = $state(ALL);
	let adviserFilter = $state(ALL);
	let localSort = $state<DataTableSort | null>(null);

	const server = $derived(!!listing && !!onquery);

	const localOrder = $derived.by(() => localSort ?? defaultSort(columns));

	const sort = $derived<DataTableSort | null>(
		server && listing ? (listing.sort ? { key: listing.sort, dir: listing.dir } : null) : localOrder
	);

	/** Sorting a paged listing locally would reorder only the rows in hand and call it the book. */
	function chooseSort(next: DataTableSort | null): void {
		if (!server || !listing || !onquery) {
			localSort = next;
			return;
		}
		onquery({ ...listing, sort: next?.key ?? null, dir: next?.dir ?? 'asc', offset: 0 });
	}
	const term = $derived(listing ? listing.q : query);

	const categoryLabel = (value: string) =>
		CONTRIBUTION_CATEGORIES.find((c) => c.value === value)?.label ?? value;
	/** "Personal" / "Employer": the booking vocabulary's short labels, for a narrow table. */
	const shortCategory = (value: string) =>
		CREATABLE_CONTRIBUTION_CATEGORIES.find((c) => c.value === value)?.label ??
		(value === 'third_party_contribution' ? 'Third-party' : categoryLabel(value));

	/** The status filter speaks STAGES, not statuses — the same six the KPI strip counts, plus the
	 *  terminals. It replaces the clickable pipeline strip that used to sit above the queue: a
	 *  filter belongs with the other filters, and having it in two places meant two controls could
	 *  disagree about what was narrowed. The value on the wire is the stage KEY, which is what the
	 *  API's own `status` filter and facet now speak (`app/contributions.py::stage_of`). */
	const stageLabel = (value: string) =>
		STAGES.find((stage) => stage.key === value)?.label ?? humanise(value);
	/** Pipeline order, never alphabetical: the options ARE the lifecycle, and "Completed" sorting
	 *  above "Not lodged" reads as a list of unrelated words. */
	const stageOrder = (value: string) => {
		const at = STAGES.findIndex((stage) => stage.key === value);
		return at === -1 ? STAGES.length : at;
	};
	const byStage = (
		options: { value: string; label: string }[]
	): { value: string; label: string }[] =>
		[...options].sort((a, b) => stageOrder(a.value) - stageOrder(b.value));

	// Facets describe only the booked rows, so they're merged with the detected credits on screen.
	const statusOptions = $derived(
		byStage(
			mergeOptions(
				server ? optionsFromFacet(facets, 'status', stageLabel) : [],
				optionsFrom(rows, (c) => stageKeyOf(c.status), stageLabel)
			)
		)
	);

	const categoryOptions = $derived(
		mergeOptions(
			server ? optionsFromFacet(facets, 'category', categoryLabel) : [],
			optionsFrom(rows, (c) => c.category, categoryLabel)
		)
	);
	const adviserOptions = $derived(
		mergeOptions(
			server ? optionsFromFacet(facets, 'adviser') : [],
			optionsFrom(rows, (c) => c.adviser)
		)
	);

	// NOTE: the local-mode search box is NOT in here — it is `term` — so anything asking "is the
	// list narrowed" must ask `dirty || term`. `ListingToolbar` shows its own Clear on `dirty ||
	// query`, so using `dirty` alone lets the toolbar and the empty state disagree on one screen.
	const BAR_KEYS = ['status', 'category', 'adviser'] as const;

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'status', label: 'Status', options: statusOptions },
		{ key: 'category', label: 'Contribution type', options: categoryOptions },
		{
			key: 'adviser',
			label: 'Adviser',
			options: adviserOptions,
			searchPlaceholder: 'Search advisers…'
		}
	]);

	const dirty = $derived(
		listing
			? isFiltered(listing)
			: statusFilter !== ALL || categoryFilter !== ALL || adviserFilter !== ALL
	);
	function clear() {
		if (listing && onquery) {
			onquery({ ...listing, q: '', filters: {}, offset: 0 });
			return;
		}
		statusFilter = ALL;
		categoryFilter = ALL;
		adviserFilter = ALL;
		query = '';
	}

	// Date leads. The line under the client is the transaction (or the contribution, when no
	// transaction has landed yet) — never the client's own id. The account is the muted name.
	// `showClient` drops the client column on a page that is already that client.
	const referenceFor = (c: Contribution): { label: string; value: string } =>
		c.transaction_reference
			? { label: 'Transaction', value: c.transaction_reference }
			: { label: 'Contribution', value: c.uid };
	// THE TABLE FOLDS BY ITS OWN WIDTH, not the viewport's. It is full width until a row is picked,
	// then the classify panel pushes in beside it and takes ~400px — so the same screen can hold a
	// 1100px table or a 550px one. Below 64rem (`@5xl`) Booked steps out. Below 56rem (`@4xl`) the
	// account folds under the client, where the transaction reference was (the open panel shows the
	// reference). Below 42rem (`@2xl`: 1280 with the sidebar and the panel open) Type steps out too;
	// the panel states it in full.
	//
	// THE COLUMNS OF A KNOWN SIZE GET IT IN REM, and the names take the rest — the clients listing's
	// pattern. Date, Amount, Type, Status and Booked each hold one short value, so a share of the row
	// gave each two or three times its content on a wide table and left a band of white beside every
	// pill. Client and Account have no width: under `table-fixed` the auto columns split whatever the
	// others leave, and the names are what can use it. The account keeps its own column down to
	// 56rem, where the two names still get ~190px each, so that room holds a name rather than a gap.
	const columns = $derived<DataTableColumn<Contribution>[]>([
		{
			key: 'date',
			// "14 Sept 2026" whole, plus the cell's padding.
			headerClass: 'w-[6.5rem]',
			header: 'Date',
			sortable: true,
			sortDefault: true,
			defaultDir: 'desc',
			sortValue: (c) => c.contribution_date ?? c.created_at
		},
		...(showClient
			? [
					{
						key: 'client',
						header: 'Client',
						sortable: true,
						sortValue: (c: Contribution) => c.client_name ?? ''
					} satisfies DataTableColumn<Contribution>
				]
			: []),
		{
			key: 'account',
			header: 'Account',
			sortable: true,
			sortValue: (c) => c.account_name ?? '',
			// Folds under the client only when there IS a client column to fold under. Without one the
			// account is the row's name and must never hide.
			...(showClient ? { hideBelowWidth: '4xl' as const } : {})
		},
		{
			key: 'amount',
			// A seven-figure sum ("£1,234,567.89") and the muted gross under it.
			headerClass: 'w-[7.5rem]',
			header: 'Amount',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (c) => c.amount
		},
		{
			key: 'category',
			// The short word ("Personal") while the table is narrow, the full label from 64rem — the
			// same switch the cell makes. A longer one truncates inside its pill, whole on hover.
			headerClass: 'w-[8rem] @5xl:w-[11rem]',
			header: 'Type',
			sortable: true,
			hideBelowWidth: '2xl'
		},
		// FREQUENCY, TAX YEAR AND EXPECTATION ARE GONE, and none of them is coming back as a column.
		// Frequency and tax year are properties of the contribution that never vary within a row an
		// adviser is triaging — they are on the record, and the queue is for deciding what to look
		// at next. The expectation badge answered "has the custodian been told", which is now the
		// Not lodged metric and a status stage; two spellings of one fact in one table is how the
		// KPI and pipeline strips came to disagree.
		{
			key: 'status',
			header: 'Status',
			// The widest cell is "Relief in transit" (status.ts) in a `whitespace-nowrap` badge
			// plus the ML chip beside it, 143px it cannot give up. Too narrow and the column
			// cannot shrink to its own content — under `table-fixed` that means the column wins and
			// the TABLE grows past its container, and the workbench scrolls sideways because of one
			// status. 10rem is that plus the cell padding, at every fold.
			sortable: true,
			// Sorted by STAGE, not by the raw status, so the order matches the words on screen and
			// the filter above it. Sorting by `status` put "confirmed" between "awaiting…" and
			// "identified" while all three read as two stages.
			sortValue: (c) => stageKeyOf(c.status) ?? '',
			headerClass: 'w-[10rem]'
		},
		{
			key: 'created_at',
			headerClass: 'w-[6.5rem]',
			header: 'Booked',
			sortable: true,
			defaultDir: 'desc',
			hideBelowWidth: '5xl'
		}
	]);

	/** The classes that show the account under the client exactly while its column is folded away.
	 *  Undefined when it never folds (no client column), and then the reference keeps that line. */
	const accountFolded = $derived(visibleWhenHidden(columns.find((col) => col.key === 'account')));

	const visible = $derived.by(() => {
		if (!server) {
			return sortRows(
				rows.filter(
					(c) =>
						matches(statusFilter, stageKeyOf(c.status)) &&
						matches(categoryFilter, c.category) &&
						matches(adviserFilter, c.adviser) &&
						search(query, c.uid, c.account_name, c.client_name, c.adviser, c.employer_name)
				),
				columns,
				sort
			);
		}
		// The detected credits that belong on THIS page (`detectedOnPage`: the query narrows them,
		// and only the first page carries them), sorted locally and kept in front of the
		// server-ordered booked rows. The workbench's pager counts from the same function, so the
		// label and the rows cannot disagree.
		const detected = listing ? detectedOnPage(rows, listing) : [];
		return [...sortRows(detected, columns, sort), ...rows.filter((c) => !isDetected(c.uid))];
	});
</script>

{#snippet noContributions()}
	<EmptyState
		compact
		title="No contributions yet"
		description="Open a pension account and choose Record a contribution."
	/>
{/snippet}

<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
	<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
		<ListingToolbar
			class="shrink-0"
			query={term}
			placeholder="Search reference, client, account, adviser or employer"
			{dirty}
			onclear={clear}
			onsearch={listing && onquery
				? (next) => onquery({ ...listing, q: next, offset: 0 })
				: undefined}
			{filterDefs}
			filterValue={barValueFrom(listing, BAR_KEYS)}
			onfilters={(next) => listing && onquery && onquery(withBarValue(listing, BAR_KEYS, next))}
		>
			{#snippet filters()}
				{#if !server}
					<FilterSelect
						bind:value={statusFilter}
						options={statusOptions}
						allLabel="All active statuses"
						label="Filter by status"
					/>
					<FilterSelect
						bind:value={categoryFilter}
						options={categoryOptions}
						allLabel="All types"
						label="Filter by contribution type"
					/>
					<FilterSelect
						bind:value={adviserFilter}
						options={adviserOptions}
						allLabel="All advisers"
						label="Filter by adviser"
					/>
				{/if}
			{/snippet}
		</ListingToolbar>

		<!-- WHICH NOTHING, and where the way out lives: the table's `empty` said "No contributions
		     match your filters." unconditionally, underneath the workbench's own "No contributions
		     yet" Alert — two contradictory answers on one screen, one blaming a filter nobody had
		     set. That Alert's guidance moved into the empty state, where the rows would have been. -->
		<DataTable
			scroll
			fixed
			{columns}
			rows={visible}
			{sort}
			onSort={chooseSort}
			getKey={(c) => c.uid}
			empty={dirty || term ? 'No contributions match your filters.' : noContributions}
			rowClass={(c) =>
				[
					// The tint, plus the sidebar's active rule down the leading edge, so the selected row
					// still reads as THE row after the panel opens and the table reflows around it.
					// `contribution-selected` carries no style: it is how the workbench finds the row to
					// scroll back into view.
					c.uid === selected
						? 'contribution-selected bg-accent/60 [&>td:first-child]:shadow-[inset_2px_0_0_0_var(--primary)]'
						: '',
					c.status === 'manual_intervention' ? 'bg-destructive/10' : ''
				]
					.filter(Boolean)
					.join(' ')}
			onRowClick={(c) => (selected = c.uid)}
		>
			{#snippet cell(c, column)}
				{#if column.key === 'client'}
					{@const reference = referenceFor(c)}
					<!-- Linked when the mount injects a client route. A real `<a href>`, so middle-click
					     opens the record without losing the queue. DataTable exempts an in-cell anchor
					     from `onRowClick`. `items-start` keeps the name as wide as its text, and
					     `max-w-full` caps it at the column: `w-fit` alone on a `truncate` box resolves to
					     the text's full nowrap width, which is what let a long name print over the next
					     column instead of cutting off. Cut by the column's width, not a character count,
					     so a long account name uses the room the row has. The reference is `w-full`: a
					     transaction id has to stay inside this column, not paint into Account. -->
					<span class="flex w-full min-w-0 flex-col items-start">
						<TruncatedText
							text={c.client_name ?? '—'}
							href={links && c.contact_uid ? links.client(c.contact_uid) : undefined}
							class="w-fit max-w-full font-medium"
						/>
						{#if accountFolded}
							<!-- While the Account column is folded away the account takes this line, and the
							     reference gives way to it: the table is narrow because the panel is open, and
							     the panel shows the reference. -->
							<TruncatedText
								text={c.account_name ?? '—'}
								href={links?.account && c.account_uid && c.account_name
									? links.account(c.account_uid)
									: undefined}
								class="text-muted-foreground w-fit max-w-full text-[10.5px] {accountFolded}"
							/>
							<span class="hidden w-full @4xl:block">
								<RecordId
									label={reference.label}
									value={reference.value}
									class="w-full text-[10.5px]"
								/>
							</span>
						{:else}
							<RecordId
								label={reference.label}
								value={reference.value}
								class="w-full text-[10.5px]"
							/>
						{/if}
					</span>
				{:else if column.key === 'account'}
					<!-- The dash is never linked: an account with no name is nothing to click. Muted, and
					     only as wide as the name — the same treatment as the transfers queue. -->
					<TruncatedText
						text={c.account_name ?? '—'}
						href={links?.account && c.account_uid && c.account_name
							? links.account(c.account_uid)
							: undefined}
						class="text-muted-foreground w-fit max-w-full"
					/>
				{:else if column.key === 'date'}
					<span class="whitespace-nowrap tabular-nums">{formatDate(c.contribution_date)}</span>
				{:else if column.key === 'amount'}
					<Money amount={c.amount} class="font-medium" />
					{#if c.relief_amount}
						<span class="text-muted-foreground block text-[10.5px]">
							gross <Money amount={c.gross_amount ?? c.amount} />
						</span>
					{/if}
				{:else if column.key === 'category'}
					<!-- Both lines are capped to the column: the category pill truncates inside the Badge, the
					     employer line is a `TruncatedText`, so the full value stays on hover and focus. -->
					<span class="flex min-w-0 flex-col gap-0.5">
						<!-- Narrow (the same 64rem fold as the account): the one word that decides the tax
						     treatment, "Personal" or "Employer", which are the words the panel's switch uses.
						     The full label stays on hover. -->
						<Badge variant="outline" class="max-w-full min-w-0 font-normal"
							><TruncatedText
								text={categoryLabel(c.category)}
								class="hidden @5xl:block"
							/><TruncatedText
								text={shortCategory(c.category)}
								full={categoryLabel(c.category)}
								class="@5xl:hidden"
							/></Badge
						>
						{#if c.paid_by !== 'client'}
							<TruncatedText
								text={c.employer_name ?? c.paid_by.replace(/_/g, ' ')}
								class="text-muted-foreground max-w-full min-w-0 text-[10px] tracking-wide uppercase"
							/>
						{/if}
					</span>
				{:else if column.key === 'status'}
					<StatusBadge status={c.status} />
					{#if c.ml_contribution_id}
						<Badge variant="outline" class="ml-1 font-normal">ML</Badge>
					{/if}
				{:else if column.key === 'created_at'}
					<span class="text-muted-foreground whitespace-nowrap tabular-nums">
						{formatDate(c.created_at)}
					</span>
				{/if}
			{/snippet}
		</DataTable>
		{@render pager?.()}
	</CardContent>
</Card>
