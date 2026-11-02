<script lang="ts">
	// The accounts book: every account the caller may see, filterable the way staff actually look
	// one up (status/adviser/custodian/product type/service/model + free text). Extracted verbatim from the
	// platform portal's /accounts page — data comes in as a prop (the app's loader owns the
	// fetch) and links come in as builders (the app owns its route tree), so the admin portal
	// mounts the same surface over its own loader and routes.
	import { Card, CardContent } from '../../ui/card/index.js';
	import { DataTable } from '../../ui/data-table/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Button } from '../../ui/button/index.js';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import { defaultSort, sortRows } from '../../ui/data-table/index.js';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { Money } from '../../ui/money/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import AdviserStack from '../../data/adviser-stack.svelte';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { Badge } from '../../ui/badge/index.js';
	import { CurrencyBadge } from '../../ui/currency-badge/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { statusTone } from '../listing/status-tone.js';
	import FilterSelect from '../listing/filter-select.svelte';
	import { type FilterDef } from '../listing/filter-panel.svelte';
	import ListingToolbar from '../listing/listing-toolbar.svelte';
	import Pagination from '../listing/pagination.svelte';
	import PaginationStrip from '../listing/pagination-strip.svelte';
	import ExportMenu from '../listing/export-menu.svelte';
	import { ALL, humanise, matches, optionsFrom, search } from '../listing/filters.js';
	import {
		barValueFrom,
		isFiltered,
		withBarValue,
		optionsFromFacet,
		PAGE_SIZE,
		rangeFromBounds,
		rangeLabelFromOffset,
		type ListingQuery
	} from '../listing/query.js';
	import { custodianLabel } from '../listing/custodian.js';
	import { clientName, providerReference } from './format.js';
	import { serviceTypeLabel } from './service-type.js';
	import type { Account, AccountBookLinks, AccountFilterOption } from './types.js';

	let {
		accounts,
		links,
		listing = null,
		matchCount = null,
		facets,
		bounds,
		productTypeOptions,
		modelOptions,
		onquery,
		pageHref,
		exportHref
	}: {
		accounts: Account[];
		links: AccountBookLinks;
		/** SERVER-DRIVEN when supplied: `accounts` is one page, the API applied the search and
		 *  filters, and this component only ASKS for changes via `onquery`. Absent ⇒ the original
		 *  local mode, which is still right for a complete list or a fixture set. */
		listing?: ListingQuery | null;
		/** Matches across the whole book. Named `matchCount` because `total` here has always
		 *  meant the summed VALUE of the rows, and two totals with one name is how a pager ends
		 *  up reporting millions of pages. */
		matchCount?: number | null;
		facets?: Record<string, string[]>;
		bounds?: Record<string, number[]>;
		/** Authoritative options from the tenant's enabled product shelf. When absent (the admin's
		 *  complete-array mount), options are derived from the accounts supplied. */
		productTypeOptions?: AccountFilterOption[];
		/** Authoritative options from the tenant's enabled model catalogue. */
		modelOptions?: AccountFilterOption[];
		onquery?: (next: ListingQuery) => void;
		/** Builds the URL for a 1-indexed page, so the pager renders real links (shareable,
		 *  middle-clickable, back-button friendly). Injected because only the app knows its own
		 *  route and which unrelated params must survive — the same reason `links` is injected. */
		pageHref?: (page: number) => string;
		/** Download the CURRENT filter set as CSV. Absent ⇒ no Export button. */
		exportHref?: string;
	} = $props();

	const server = $derived(!!listing && !!onquery);

	/** The filters the bar owns, in the order it offers them. */
	const BAR_KEYS = ['status', 'adviser', 'custodian', 'service', 'type', 'model', 'value'] as const;
	const RANGE_KEYS: readonly string[] = ['value'];

	let localQuery = $state('');
	let statusFilter = $state(ALL);
	let adviserFilter = $state(ALL);
	let typeFilter = $state(ALL);
	let serviceFilter = $state(ALL);
	let modelFilter = $state(ALL);
	// Null — the table's own default column, as everywhere else. See `clients-listing`.
	let localSort = $state<DataTableSort | null>(null);
	const localOrder = $derived.by(() => localSort ?? defaultSort(columns));

	/** In SERVER mode the sort mirrors the query — the API ordered, so the rows arrive ready. */
	const sort = $derived<DataTableSort | null>(
		server && listing ? (listing.sort ? { key: listing.sort, dir: listing.dir } : null) : localOrder
	);

	/** Hand a column click to the SERVER: ordering the page in hand reorders 25 rows and calls it
	 *  "by value", while the largest account in the book may be pages away and never move. */
	function chooseSort(next: DataTableSort | null): void {
		if (!server || !listing || !onquery) {
			localSort = next;
			return;
		}
		onquery({ ...listing, sort: next?.key ?? null, dir: next?.dir ?? 'asc', offset: 0 });
	}

	const owner = (a: Account) => (a.client.id == null ? '—' : clientName(a.client));
	const term = $derived(listing ? listing.q : localQuery);

	// Server-driven, the options come from the API's facets: derived from the rows on screen they
	// could only offer values already visible, so paging would change what you may filter by.
	const statusOptions = $derived(
		server
			? optionsFromFacet(facets, 'status', humanise)
			: optionsFrom(accounts, (a) => a.status, humanise)
	);
	const adviserOptions = $derived(
		server ? optionsFromFacet(facets, 'adviser') : optionsFrom(accounts, (a) => a.adviser)
	);
	const custodianOptions = $derived(
		server
			? optionsFromFacet(facets, 'custodian', custodianLabel)
			: optionsFrom(accounts, (a) => a.custodian, custodianLabel)
	);
	const typeOptions = $derived(
		productTypeOptions ??
			(server
				? optionsFromFacet(facets, 'type', humanise)
				: optionsFrom(accounts, (a) => a.product_type, humanise))
	);
	/** The label is `serviceTypeLabel`'s, so the filter and the column read the same word for the
	 *  same account; `humanise` is the fallback for a value the vocabulary does not know, which is
	 *  better than dropping it from a filter built off the book's own rows. */
	const serviceLabel = (value: string) => serviceTypeLabel(value) ?? humanise(value);
	const serviceOptions = $derived(
		server
			? optionsFromFacet(facets, 'service', serviceLabel)
			: optionsFrom(accounts, (a) => a.service_type, serviceLabel)
	);
	const availableModelOptions = $derived(
		modelOptions ??
			optionsFrom(
				accounts.filter((a) => a.model_id != null),
				(a) => String(a.model_id),
				(value) => accounts.find((a) => String(a.model_id) === value)?.model_name ?? value
			)
	);

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'status', label: 'Status', options: statusOptions },
		{
			key: 'adviser',
			label: 'Adviser',
			options: adviserOptions,
			searchPlaceholder: 'Search advisers…'
		},
		{ key: 'custodian', label: 'Custodian', options: custodianOptions },
		{ key: 'service', label: 'Service type', options: serviceOptions },
		{ key: 'type', label: 'Product type', options: typeOptions },
		{
			key: 'model',
			label: 'Model',
			options: availableModelOptions,
			searchPlaceholder: 'Search models…'
		},
		// `cash`/`growth` stay unfilterable: both are lake-derived and merged onto a row only after
		// the page cut, so a bound over either would need to read the lake for the whole match set.
		{
			key: 'value',
			label: 'Value',
			options: [],
			kind: 'numberrange',
			currency: 'GBP',
			...(rangeFromBounds(bounds, 'value') ?? {})
		}
	]);

	const dirty = $derived(
		listing
			? isFiltered(listing)
			: statusFilter !== ALL ||
					adviserFilter !== ALL ||
					typeFilter !== ALL ||
					serviceFilter !== ALL ||
					modelFilter !== ALL
	);

	/**
	 * WHICH NOTHING THE TABLE IS SHOWING, and the test was on the wrong thing.
	 *
	 * It read `accounts.length === 0 ? 'No accounts yet.' : 'No accounts match your filters.'` — but
	 * in SERVER mode `accounts` is ONE PAGE of results, so a filter matching nothing empties it and
	 * the firm holding 379 accounts was told it had none. The question is the state of the FILTERS,
	 * which is what `dirty` answers, and it is right in both modes.
	 */
	// AND THE SEARCH TERM IS PART OF THE ANSWER. In local mode `dirty` tests the five dropdowns
	// only, while `term` lives in `localQuery` — so a search matching nothing left the title saying
	// "No accounts yet" beside a Clear button the action arm (`dirty || term`) did render: the two
	// halves of one state disagreeing about which state it was. Everything now reads `narrowed`.
	const narrowed = $derived(dirty || term.trim() !== '');
	const emptyTitle = $derived(narrowed ? 'No accounts match your filters' : 'No accounts yet');
	/** An account is opened through an application or arrives from the custodian's feed, so the
	 *  empty-book branch has no "add one" button to point at — inventing one would be the dead end
	 *  Carbon's pattern names. The narrowed branch had NO BODY at all, which is half a state: it now
	 *  says what clearing gets back. */
	const emptyDescription = $derived(
		narrowed
			? 'Try a different search or filter, or clear them to see every account in the book.'
			: 'Accounts appear as applications complete and the custodian’s feed lands.'
	);
	function clear() {
		if (listing && onquery) {
			onquery({ ...listing, q: '', filters: {}, offset: 0 });
			return;
		}
		statusFilter = ALL;
		adviserFilter = ALL;
		typeFilter = ALL;
		serviceFilter = ALL;
		modelFilter = ALL;
		// AND THE SEARCH BOX — which, until this was traced, could not hold anything to reset.
		// `ListingToolbar`'s `query` is `$bindable` and all three listings pass it UNBOUND (`term`
		// is a $derived), so the toolbar's write landed on its own local override and never reached
		// the parent; with no `onsearch` in local mode it also never called back. `localQuery` was
		// therefore permanently '' and LOCAL-MODE FREE-TEXT SEARCH FILTERED NOTHING on the accounts,
		// transactions and clients listings — a box you can type in that does not narrow the table,
		// which is worse than no box. The toolbar now gets an `onsearch` in local mode too, so the
		// term reaches `localQuery`, the `search(...)` predicates below finally run against it, and
		// this reset has something to undo.
		localQuery = '';
	}

	const rows = $derived(
		server
			? accounts
			: accounts.filter(
					(a) =>
						matches(statusFilter, a.status) &&
						matches(adviserFilter, a.adviser) &&
						matches(typeFilter, a.product_type) &&
						matches(serviceFilter, a.service_type) &&
						matches(modelFilter, a.model_id == null ? null : String(a.model_id)) &&
						search(localQuery, a.name, a.uid, owner(a), a.adviser, a.product_name, a.model_name)
				)
	);
	// The read-only/admin mount receives the complete array rather than a server page. It still
	// follows the same 25-row contract; only the table body scrolls, while this pager remains fixed.
	let localPage = $state(1);
	const localPageCount = $derived(Math.max(1, Math.ceil(rows.length / PAGE_SIZE)));
	$effect(() => {
		void localQuery;
		void statusFilter;
		void adviserFilter;
		void typeFilter;
		void serviceFilter;
		void modelFilter;
		localPage = 1;
	});
	$effect(() => {
		if (localPage > localPageCount) localPage = localPageCount;
	});

	// Ten columns. Model is first-class rather than inferred from the old Advisory/Discretionary
	// filter: two discretionary accounts may follow different mandates, and that distinction is what
	// staff need to scan. It is plain truncated text rather than the old wide badge.
	//
	// Every free-text column now carries a CEILING (text-shell.ts), because a `<td>` is
	// `whitespace-nowrap` under `table-layout: auto` -- so a column is as wide as its longest value
	// and one 89-character fund name made every row that wide.
	//
	// EVERY COLUMN DECLARES ITS SHARE and the table is `fixed`. Ceilings alone could not space these
	// evenly: under `auto` layout the browser hands leftover width out in proportion to CONTENT, so
	// Account and Client took most of it while Type stayed at the width of "SIPP". A cap stops a column
	// growing past a limit; it does not stop it claiming slack below one.
	const columns: DataTableColumn<Account>[] = [
		{
			key: 'name',
			headerClass: 'w-[13%]',
			header: 'Account',
			sortable: true,
			sortDefault: true,
			cellClass: 'truncate',
			sortValue: (a) => a.name ?? providerReference(a.uid) ?? ''
		},
		{
			key: 'client',
			headerClass: 'w-[10%]',
			header: 'Client',
			sortable: true,
			cellClass: 'truncate',
			sortValue: owner
		},
		{
			key: 'adviser',
			headerClass: 'w-[8%]',
			header: 'Adviser',
			sortable: true,
			hideBelow: 'lg',
			cellClass: 'truncate'
		},
		// THE THREE CLASSIFICATION COLUMNS SIT TOGETHER, and in this order: how the account is
		// SERVICED, what it IS, and what it is run TO. Service type leads them because it is the
		// firm's relationship with the account and it governs what may be done to it, where the
		// wrapper and the model are properties of the account itself. Type used to lead the whole
		// table with Model two name columns away, so the halves of one answer — "a SIPP, run to
		// Balanced Growth, on a discretionary basis" — were read from opposite ends of the row.
		{
			// The SERVICE, not the mandate: a discretionary account is run to a model, an advisory one
			// is recommended to, an execution-only one is neither. The API decides the value (the
			// captured investment capacity, else `discretionary` for a model-run account, else
			// nothing) so this column and the filter beside it cannot mean different things.
			key: 'service',
			headerClass: 'w-[9%]',
			header: 'Service type',
			sortable: true,
			hideBelow: 'lg',
			cellClass: 'truncate',
			sortValue: (a) => a.service_type
		},
		{
			// No `hideBelow`: four characters wide, and it is the first thing anyone asks about an
			// account after whose it is.
			key: 'type',
			headerClass: 'w-[6%]',
			header: 'Type',
			sortable: true,
			sortValue: (a) => a.product_type
		},
		{
			key: 'model',
			headerClass: 'w-[8%]',
			header: 'Model',
			sortable: true,
			hideBelow: 'lg',
			cellClass: 'truncate',
			sortValue: (a) => a.model_name
		},
		{
			// The account's base currency, as an ISO code. The symbol sits on the figures;
			// `$` alone does not say which dollar.
			key: 'currency',
			headerClass: 'w-[8%]',
			header: 'Currency',
			sortable: true,
			sortValue: (a) => a.currency
		},
		{
			key: 'value',
			headerClass: 'w-[11%]',
			header: 'Value',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (a) => a.value
		},
		{
			key: 'cash',
			headerClass: 'w-[9%]',
			header: 'Cash',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelow: 'md'
		},
		{
			key: 'growth',
			headerClass: 'w-[10%]',
			header: 'Growth',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelow: 'md'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[8%]'
		}
	];

	// Server mode: ordered and cut by the API. Local mode: sort the WHOLE population, then slice —
	// `DataTable` stops sorting once `onSort` is passed, and it could only ever have ordered the
	// page it was handed anyway.
	const displayedRows = $derived(
		server
			? rows
			: sortRows(rows, columns, localOrder).slice(
					(localPage - 1) * PAGE_SIZE,
					localPage * PAGE_SIZE
				)
	);
	const localRange = $derived(
		rangeLabelFromOffset((localPage - 1) * PAGE_SIZE, displayedRows.length, rows.length)
	);
</script>

{#snippet clearFilters()}
	<Button variant="outline" size="sm" onclick={clear}>Clear filters</Button>
{/snippet}

{#snippet nothingToShow()}
	<!-- The way out was already in this file — `clear()` is wired to the toolbar's own Clear — and
	     the empty state hardcoded `action={undefined}`. Its twin, `transactions-listing`, was
	     written at the same time and got this right; only one of the pair did. An empty BOOK keeps
	     no action, deliberately: an account arrives from a completed application or the custodian's
	     feed, and `AccountBookLinks` has no create builder to point at. -->
	<EmptyState
		compact
		icon={WalletIcon}
		title={emptyTitle}
		description={emptyDescription}
		action={narrowed ? clearFilters : undefined}
	/>
{/snippet}

<!-- ONE SCREEN from `md`. The page is a flex column the height of the viewport under the top bar,
     and the LIST is the only thing that scrolls — so the toolbar and the pager stay put however
     long the book is. On a phone the PAGE scrolls instead. The topbar crumb already names the
     page, so there is no heading. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			<ListingToolbar
				class="shrink-0"
				query={term}
				placeholder="Search accounts, reference, client or adviser"
				{dirty}
				onclear={clear}
				onsearch={listing && onquery
					? (next) => onquery({ ...listing, q: next, offset: 0 })
					: (next) => (localQuery = next)}
				{filterDefs}
				filterValue={barValueFrom(listing, BAR_KEYS, RANGE_KEYS)}
				onfilters={(next) =>
					listing && onquery && onquery(withBarValue(listing, BAR_KEYS, next, RANGE_KEYS))}
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
							bind:value={adviserFilter}
							options={adviserOptions}
							allLabel="All advisers"
							label="Filter by adviser"
						/>
						<FilterSelect
							bind:value={serviceFilter}
							options={serviceOptions}
							allLabel="All services"
							label="Filter by service type"
						/>
						<FilterSelect
							bind:value={typeFilter}
							options={typeOptions}
							allLabel="All types"
							label="Filter by product type"
						/>
						<FilterSelect
							bind:value={modelFilter}
							options={availableModelOptions}
							allLabel="All models"
							label="Filter by model"
						/>
					{/if}
				{/snippet}
			</ListingToolbar>

			<DataTable
				{columns}
				rows={displayedRows}
				{sort}
				onSort={chooseSort}
				scroll
				fixed
				getKey={(a) => a.id}
				label="Accounts"
				empty={nothingToShow}
			>
				{#snippet cell(a, column)}
					{#if column.key === 'name'}
						<span class="block min-w-0">
							<TruncatedText
								text={a.name ?? providerReference(a.uid) ?? 'Account'}
								href={links.account(a.uid)}
								class="font-medium"
							/>
							<RecordId value={providerReference(a.uid)} />
						</span>
					{:else if column.key === 'client'}
						{#if a.client.uid == null}
							<span class="text-muted-foreground">—</span>
						{:else}
							<TruncatedText text={clientName(a.client)} href={links.client(a.client.uid)} />
						{/if}
					{:else if column.key === 'type'}
						{#if a.product_type}
							<!-- Capped at the column, as Service type is beside it. The column is sized for
							     SIPP and ISA, so a third-party wrapper's code (TPOFFBOND) printed over the
							     Model column next to it. It truncates now, with the whole code on hover. -->
							<Badge variant="outline" class="max-w-full min-w-0 font-normal"
								><TruncatedText text={humanise(a.product_type)} /></Badge
							>
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					{:else if column.key === 'service'}
						<!-- An em-dash, never a guess. An account fed by a custodian with no model carries
						     neither the captured capacity nor a mandate, and advisory and execution-only
						     are indistinguishable from what we hold — so the cell says we do not know
						     rather than putting a service agreement on screen that nobody agreed to. -->
						{#if serviceTypeLabel(a.service_type)}
							<Badge variant="outline" class="max-w-full min-w-0 font-normal"
								><TruncatedText text={serviceTypeLabel(a.service_type) ?? ''} /></Badge
							>
						{:else}
							<span class="text-muted-foreground text-xs">—</span>
						{/if}
					{:else if column.key === 'adviser'}
						<AdviserStack names={[a.adviser]} />
					{:else if column.key === 'model'}
						{#if a.model_name}
							<TruncatedText text={a.model_name} />
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					{:else if column.key === 'currency'}
						{#if a.currency}
							<CurrencyBadge code={a.currency} />
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					{:else if column.key === 'value'}
						<Money amount={a.value} currency={a.currency || 'GBP'} class="font-medium" />
					{:else if column.key === 'cash'}
						<Money amount={a.cash} currency={a.currency || 'GBP'} />
					{:else if column.key === 'growth'}
						<Money amount={a.growth} currency={a.currency || 'GBP'} signed tone="directional" />
					{:else if column.key === 'status'}
						<StatusBadge tone={statusTone(a.status)}>{humanise(a.status)}</StatusBadge>
					{/if}
				{/snippet}
			</DataTable>

			{#if listing && onquery && matchCount !== null}
				<div class="shrink-0">
					<Pagination
						query={listing}
						total={matchCount}
						shown={rows.length}
						href={pageHref}
						exportHref={exportHref ?? undefined}
					/>
				</div>
			{:else if rows.length > 0}
				<PaginationStrip label={localRange} bind:page={localPage} pageCount={localPageCount} />
			{:else if exportHref}
				<!-- No pager (a locally paged or already-complete list), but the export still belongs at
				     the card's foot — same border, same corner, so its position does not move with the
				     mount. -->
				<div class="-mx-4 flex shrink-0 items-center justify-end border-t px-4 pt-3">
					<ExportMenu href={exportHref} label="Export" />
				</div>
			{/if}
		</CardContent>
	</Card>
</div>
