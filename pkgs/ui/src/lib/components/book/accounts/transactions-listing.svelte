<script module lang="ts">
	import type { FilterDef } from '../listing/filter-panel.svelte';

	/** A pure function so it can be pinned in a test without rendering the panel. */
	export function transactionFilterDefs(input: {
		showAdviserFilter: boolean;
		showAccountFilter: boolean;
		showDateFilter?: boolean;
		statusOptions: FilterDef['options'];
		typeOptions: FilterDef['options'];
		adviserOptions: FilterDef['options'];
		accountOptions: FilterDef['options'];
		currencyOptions: FilterDef['options'];
		custodianOptions: FilterDef['options'];
		amountBounds?: { min: string; max: string };
		quantityBounds?: { min: string; max: string };
		dateMin?: string;
		dateMax?: string;
	}): FilterDef[] {
		return [
			{ key: 'status', label: 'Status', options: input.statusOptions },
			{ key: 'type', label: 'Type', options: input.typeOptions },
			...(input.showAdviserFilter
				? [
						{
							key: 'adviser',
							label: 'Adviser',
							options: input.adviserOptions,
							searchPlaceholder: 'Search advisers…'
						}
					]
				: []),
			...(input.showAccountFilter
				? [
						{
							key: 'account',
							label: 'Account',
							options: input.accountOptions,
							searchPlaceholder: 'Search accounts…'
						}
					]
				: []),
			{ key: 'custodian', label: 'Custodian', options: input.custodianOptions },
			{
				key: 'currency',
				label: 'Currency',
				options: input.currencyOptions,
				searchPlaceholder: 'Search currencies…',
				autocomplete: true
			},
			{
				key: 'amount',
				label: 'Amount',
				options: [],
				kind: 'numberrange',
				currency: 'GBP',
				...(input.amountBounds ?? {})
			},
			{
				key: 'quantity',
				label: 'Units',
				options: [],
				kind: 'numberrange',
				...(input.quantityBounds ?? {})
			},
			...(input.showDateFilter
				? [
						{
							key: 'date',
							label: 'Transaction date',
							kind: 'daterange' as const,
							options: [],
							...(input.dateMin ? { min: input.dateMin } : {}),
							...(input.dateMax ? { max: input.dateMax } : {})
						}
					]
				: [])
		];
	}
</script>

<script lang="ts">
	// The firm-wide transactions listing: toolbar + datatable over the movement rows, with the
	// categorisation work queue injected above it. Extracted verbatim from the platform
	// portal's /transactions page.
	//
	// The rows come in as a prop, and every mount now loads them from `GET /crm/transactions`
	// (`platform.transactions`, with the account/client/adviser joins). Any in-session
	// categorisation overlay is the app's state too — it maps the rows before passing them, so
	// `transactions` stays the single source of truth here.
	//
	// `actionRequired` is the injected work queue (it opens the app's contribution form, an
	// ACTION); absent ⇒ no queue, the listing reads as read-only.
	//
	// A selected row opens in one of two ways (`detail`). The DRAWER is the default and what every
	// embedded mount keeps: an overlay `Sheet`, because a client's section and an account's band
	// have no width to give a panel. The PANEL is the firm ledger's (layout A, the Contributions
	// workbench's own): pushed in beside the table as a flex sibling, so the table narrows and
	// nothing covers it, the clicked row stays tinted and the next one is a click away. X and Esc
	// close it. Below `xl` there is no room for both, so the panel takes the table's place until it
	// is closed. Both show the same content (`detailBody`).
	import { tick, type Snippet } from 'svelte';
	import { cn } from '../../../utils.js';
	import { Badge } from '../../ui/badge/index.js';
	import { CurrencyBadge, CurrencyFlag } from '../../ui/currency-badge/index.js';
	import PersonAvatar from '../../data/person-avatar.svelte';
	import { Card, CardContent } from '../../ui/card/index.js';
	import * as Sheet from '../../ui/sheet/index.js';
	import { DataTable, sortRows, visibleWhenHidden } from '../../ui/data-table/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Button } from '../../ui/button/index.js';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import XIcon from '@lucide/svelte/icons/x';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { Money } from '../../ui/money/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import FilterSelect from '../listing/filter-select.svelte';
	import ListingToolbar from '../listing/listing-toolbar.svelte';
	import { ALL, humanise, matches, optionsFrom, search } from '../listing/filters.js';
	import Pagination from '../listing/pagination.svelte';
	import ExportMenu from '../listing/export-menu.svelte';
	import {
		barValueFrom,
		isFiltered,
		oneFilter,
		optionsFromFacet,
		rangeFromBounds,
		withBarValue,
		type ListingQuery
	} from '../listing/query.js';
	import { custodianLabel } from '../listing/custodian.js';
	import { providerReference, transactionReference } from './format.js';
	import { formatDate } from '../../../dates.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { statusTone } from '../listing/status-tone.js';
	import {
		conversionCaption,
		needsAction,
		subtypeDetail,
		type Transaction
	} from './transactions.js';
	import { transactionDetailGroups } from './transaction-detail.js';
	import type { AccountBookLinks } from './types.js';
	import { LINK_RECORD } from '../../../text-shell.js';
	import { panelSlide } from '../../../panel-motion.js';

	let {
		transactions,
		links,
		/**
		 * The Client column. Right on the firm-wide ledger, where it is what tells one movement's
		 * owner from another's — and pointless on ONE client's own section, where it repeats their
		 * name down the whole column and takes the width the account and the asset need. The same
		 * choice `HoldingsTable.showWrapper` makes for an account's own holdings.
		 */
		showClient = true,
		showAccount = true,
		showAdviserFilter = true,
		showAccountFilter = false,
		showDateFilter = false,
		searchPlaceholder = 'Search reference, account, client, adviser or asset',
		exportHref,
		embedded = false,
		fills = true,
		minHeight = '',
		detail = 'sheet',
		actionRequired,
		listing = null,
		matchCount = null,
		facets,
		bounds,
		onquery,
		pageHref
	}: {
		/** Already overlaid with any in-session categorisations — the app owns that state. */
		transactions: Transaction[];
		links: AccountBookLinks;
		showClient?: boolean;
		/** An account's OWN ledger repeats one name down the whole column, so that mount drops it —
		 *  the same argument `showClient` already makes one level up. */
		showAccount?: boolean;
		/** A client-scoped ledger already has one responsible adviser, so that mount hides this facet. */
		showAdviserFilter?: boolean;
		/** Offer an account picker. For a ledger that spans several accounts but not the whole firm —
		 *  a client's own, where "which of their accounts" is the question being asked. */
		showAccountFilter?: boolean;
		/** Inclusive from/to controls. In server mode these are forwarded as API filters. */
		showDateFilter?: boolean;
		searchPlaceholder?: string;
		/** Download all rows matching the current query as CSV. Omit to hide the export action.
		 *
		 *  Rendered by `ListingToolbar`, not here: this listing used to draw its own button inside the
		 *  filters snippet, which put the same action in a different place and a different size from
		 *  every other listing that gains one. */
		exportHref?: string | null;
		/**
		 * Rendered INSIDE another page (the account record's transactions band) rather than as a page
		 * of its own: no full-height frame, no `<h1>`, because the host already has both.
		 *
		 * It exists so that band can be this component instead of the bare table it was. Everything the
		 * page has — the search box, the type and date filters, the pager, the export — was absent
		 * there simply because it was a different piece of markup showing the same rows, and four
		 * separate features were really one missing component.
		 */
		embedded?: boolean;
		/** Embedded only: does the host give this band the remaining height? False on a stacked record
		 *  where three bands share one page and none of them may claim the space. */
		fills?: boolean;
		/** A height FLOOR for the card, as a Tailwind class. Embedded, this listing shares a column
		 *  with whatever the host puts above it — a categorisation queue, a filter banner — and
		 *  without a floor the table shrinks by however tall that happens to be today. */
		minHeight?: string;
		/**
		 * How a selected row opens. `'sheet'` (the default): the overlay drawer, for the mounts that sit
		 * inside a smaller space (a client's section, an account's band, the admin book) and have no
		 * width to spare. `'panel'`: pushed in beside the table, for a mount that owns the screen.
		 */
		detail?: 'sheet' | 'panel';
		/** The work queue above the table, given the outstanding items. Absent ⇒ hidden. */
		actionRequired?: Snippet<[Transaction[]]>;
		/** SERVER-DRIVEN when supplied: `transactions` is one page and the API applied the search
		 *  and filters. Absent ⇒ local mode, which is what the account record's band uses (one
		 *  account's recent rows, already narrowed by the API). */
		listing?: ListingQuery | null;
		matchCount?: number | null;
		facets?: Record<string, string[]>;
		bounds?: Record<string, number[]>;
		onquery?: (next: ListingQuery) => void;
		/** Builds the URL for a 1-indexed page, so the pager renders real links (shareable,
		 *  middle-clickable, back-button friendly). Injected because only the app knows its own
		 *  route and which unrelated params must survive — the same reason `links` is injected. */
		pageHref?: (page: number) => string;
	} = $props();

	const panel = $derived(detail === 'panel');

	let localQuery = $state('');
	const server = $derived(!!listing && !!onquery);
	const term = $derived(listing ? listing.q : localQuery);
	const dateFilterValue = (key: 'date_from' | 'date_to') => oneFilter(listing?.filters[key]) ?? '';

	const barKeys = $derived([
		'status',
		'type',
		...(showAdviserFilter ? ['adviser'] : []),
		...(showAccountFilter ? ['account'] : []),
		'custodian',
		'currency',
		'amount',
		'quantity'
	]);
	const RANGE_KEYS: readonly string[] = ['amount', 'quantity'];

	let statusFilter = $state(ALL);
	let adviserFilter = $state(ALL);
	let typeFilter = $state(ALL);
	let dateFrom = $state('');
	let dateTo = $state('');
	let localSort = $state<DataTableSort | null>({ key: 'date', dir: 'desc' });
	/** In SERVER mode the sort mirrors the query — the API ordered, so the rows arrive ready. */
	const sort = $derived<DataTableSort | null>(
		server && listing ? (listing.sort ? { key: listing.sort, dir: listing.dir } : null) : localSort
	);

	/** Hand a column click to the SERVER: ordering the page in hand reorders the rows on screen and
	 *  calls it "by amount", while the largest transaction in the ledger may be pages away. */
	function chooseSort(next: DataTableSort | null): void {
		if (!server || !listing || !onquery) {
			localSort = next;
			return;
		}
		onquery({ ...listing, sort: next?.key ?? null, dir: next?.dir ?? 'asc', offset: 0 });
	}

	const outstanding = $derived(needsAction(transactions));

	// Server-driven, from the API's facets: derived from the rows on screen they could only offer
	// values already visible, so paging would change what you may filter by.
	/** The badge's and the filter's word for a status. Only the long one differs from `humanise`:
	 *  "Awaiting approval" set the width of the Status column on its own. */
	const STATUS_WORD: Record<string, string> = { awaiting_approval: 'To approve' };
	const statusWord = (status: string) => STATUS_WORD[status] ?? humanise(status);
	const statusOptions = $derived(
		server
			? optionsFromFacet(facets, 'status', statusWord)
			: optionsFrom(transactions, (t) => t.status, statusWord)
	);
	const adviserOptions = $derived(
		server ? optionsFromFacet(facets, 'adviser') : optionsFrom(transactions, (t) => t.adviser)
	);
	const typeOptions = $derived(
		server
			? optionsFromFacet(facets, 'type', humanise)
			: optionsFrom(transactions, (t) => t.type, humanise)
	);
	// Names, not ids — a facet is a list the reader picks from, and the name is what the row shows.
	// Not humanised: an account name is the firm's own text, and sentence-casing it would rewrite it.
	const accountOptions = $derived(
		server
			? optionsFromFacet(facets, 'account', (v) => v)
			: optionsFrom(
					transactions,
					(t) => t.account_name,
					(v) => v
				)
	);
	// ISO codes, not humanised — "GBP" is already the word a reader types and reads back.
	const currencyOptions = $derived(
		server ? optionsFromFacet(facets, 'currency') : optionsFrom(transactions, (t) => t.currency)
	);
	const custodianOptions = $derived(
		server
			? optionsFromFacet(facets, 'custodian', custodianLabel)
			: optionsFrom(transactions, (t) => t.custodian, custodianLabel)
	);

	const filterDefs = $derived(
		transactionFilterDefs({
			showAdviserFilter,
			showAccountFilter,
			showDateFilter,
			statusOptions,
			typeOptions,
			adviserOptions,
			accountOptions,
			currencyOptions,
			custodianOptions,
			amountBounds: rangeFromBounds(bounds, 'amount'),
			quantityBounds: rangeFromBounds(bounds, 'quantity')
		})
	);

	// Round-trips [since, until] through the panel/bar only; the query keeps `date_from`/`date_to`.
	const DATE_FILTER_KEY = 'date';

	const filterValue = $derived.by(() => {
		const value = barValueFrom(listing, barKeys, RANGE_KEYS);
		if (!showDateFilter) return value;
		const since = listing ? dateFilterValue('date_from') : dateFrom;
		const until = listing ? dateFilterValue('date_to') : dateTo;
		return since || until ? { ...value, [DATE_FILTER_KEY]: [since, until] } : value;
	});

	/** One patch, not two navigations: bar keys plus the date range together. */
	function applyFilters(next: Record<string, string[]>) {
		const [since = '', until = ''] = next[DATE_FILTER_KEY] ?? [];
		if (!listing || !onquery) {
			if (showDateFilter) {
				dateFrom = since;
				dateTo = until;
			}
			return;
		}
		let query = withBarValue(listing, barKeys, next, RANGE_KEYS);
		if (showDateFilter) {
			const filters = { ...query.filters };
			if (since) filters.date_from = since;
			else delete filters.date_from;
			if (until) filters.date_to = until;
			else delete filters.date_to;
			query = { ...query, filters };
		}
		onquery(query);
	}

	const dirty = $derived(
		listing
			? isFiltered(listing)
			: statusFilter !== ALL ||
					(showAdviserFilter && adviserFilter !== ALL) ||
					typeFilter !== ALL ||
					(showDateFilter && (!!dateFrom || !!dateTo))
	);
	/**
	 * WHICH NOTHING THE TABLE IS SHOWING.
	 *
	 * It said "No transactions match your filters." unconditionally, on all three mount sites — so a
	 * client with no movements yet was told a filter was hiding them, and with nothing filtered
	 * there is not even a Clear control on screen to disprove it.
	 *
	 * `term` as well as `dirty`: in LOCAL mode `dirty` covers the selects only and the search box is
	 * separate, so a search matching nothing would otherwise read as "you have none".
	 */
	const emptyTitle = $derived(
		dirty || term
			? 'No transactions match your filters'
			: embedded
				? 'No transactions on this account yet'
				: 'No transactions yet'
	);

	/**
	 * WHY it is empty, which is the half a bare sentence in a table cell cannot carry.
	 *
	 * A filtered miss has a way out and says so (the Clear action below). A genuinely empty ledger
	 * has NO action, and inventing one would be worse than none: movements arrive from the
	 * custodian's feed, so "add a transaction" is not a thing an adviser can do and a button
	 * offering it would be a dead end. Carbon's rule for that case is to explain when the space
	 * will fill, which is what this does.
	 */
	const emptyDescription = $derived(
		dirty || term
			? 'Try a different search, date range or type, or clear them to see every movement.'
			: 'Movements appear as the custodian’s feed brings them in.'
	);

	function clear() {
		if (listing && onquery) {
			onquery({ ...listing, q: '', filters: {}, offset: 0 });
			return;
		}
		statusFilter = ALL;
		adviserFilter = ALL;
		typeFilter = ALL;
		dateFrom = '';
		dateTo = '';
		// AND THE SEARCH BOX. In local mode the term lives here rather than in the query, so a Clear
		// that reset only the selects left the text in place and the list still empty — an action
		// offered as the way out that does not take it. Harmless while nothing surfaced Clear inside
		// the empty state; a dead end the moment one did.
		localQuery = '';
	}

	const rows = $derived(
		server
			? transactions
			: transactions.filter(
					(t) =>
						matches(statusFilter, t.status) &&
						(!showAdviserFilter || matches(adviserFilter, t.adviser)) &&
						matches(typeFilter, t.type) &&
						(!showDateFilter || !dateFrom || t.date >= dateFrom) &&
						(!showDateFilter || !dateTo || t.date <= dateTo) &&
						search(localQuery, t.uid, t.account_name, t.client_name, t.adviser, t.subtype, t.asset)
				)
	);

	// The firm-wide ledger reads in the order staff asked for: when, reference, whose client and
	// account (both with their public/provider reference beneath), classification, asset/units,
	// money and state. Type and subtype share one cell and the subtype appears only when it adds
	// information. A missing asset is a cash movement, so it says "Cash" rather than showing a dash.
	//
	// THE TABLE FOLDS BY ITS OWN WIDTH (`hideBelowWidth`: `Table.Root` is the @container), not the
	// viewport's and not by whether the panel is open. The same 1440 screen holds a 1104px table or a
	// 708px one (panel shut / open), so the rungs answer the width the rows actually have. Each
	// column that leaves puts its value on a second line where it still matters:
	//   * 80rem (`7xl`): ISIN and Currency go. The ISIN takes the asset's second line, the code the
	//     amount's.
	//   * 64rem (`5xl`): Reference and Units go. The reference takes the client's second line, the
	//     units the asset's (where the ISIN was).
	//   * 56rem (`4xl`): Account goes under the client (the reference gives way: the drawer and the
	//     panel both show it) and Asset under the type.
	//   * 42rem (`2xl`): Type goes; its word sits under the amount, which is what says which way an
	//     unsigned figure moved.
	// Every rung folds EARLIER than the ones it replaced, so no mount can overlap where it did not
	// before. The one exception is the Reference on a mount without a Client column (a client's
	// section, an account's band): there is no second line to carry it, so it keeps its old 48rem.
	// The shares are of all eleven and total 100; `visibleColumns` renormalises whatever a mount
	// keeps, and `table-fixed` scales the survivors of a fold up to fill the row.
	const columns = $derived<DataTableColumn<Transaction>[]>([
		{
			key: 'date',
			header: 'Date',
			sortable: true,
			defaultDir: 'desc',
			headerClass: 'w-[8%]'
		},
		{
			key: 'uid',
			header: 'Reference',
			sortable: true,
			hideBelowWidth: showClient ? '5xl' : '3xl',
			headerClass: 'w-[8%]',
			cellClass: 'truncate'
		},
		{
			key: 'client',
			header: 'Client',
			sortable: true,
			headerClass: 'w-[15%]',
			cellClass: 'truncate',
			sortValue: (t) => t.client_name
		},
		{
			key: 'account',
			header: 'Account',
			sortable: true,
			headerClass: 'w-[11%]',
			cellClass: 'truncate',
			sortValue: (t) => t.account_name,
			// Folds under the client only when there IS a client column to fold under. Without one
			// the account is the row's name and must never hide.
			...(showClient ? { hideBelowWidth: '4xl' as const } : {})
		},
		{
			key: 'type',
			header: 'Type',
			sortable: true,
			hideBelowWidth: '2xl',
			headerClass: 'w-[11%]',
			cellClass: 'truncate'
		},
		{
			key: 'asset',
			header: 'Asset',
			sortable: true,
			hideBelowWidth: '4xl',
			headerClass: 'w-[9%]',
			cellClass: 'truncate'
		},
		{
			// "Units" rather than "Qty": these are units of a security, and the ledger is read
			// alongside a holdings table that has always called them that.
			//
			// LEFT, not right, though it is a column of figures — an authored exception to the rule
			// that a column of figures shares a decimal point. Units sits directly beside ISIN, and
			// the two are empty on exactly the same rows (a cash movement has neither), so a
			// right-aligned Units put its placeholder hard against ISIN's left-aligned one and the
			// pair read as ragged down the whole table. The comparison this column loses is a weak
			// one: units of different securities are not comparable quantities the way a column of
			// money is. Amount, which IS money, keeps its right alignment.
			key: 'quantity',
			header: 'Units',
			sortable: true,
			hideBelowWidth: '5xl',
			defaultDir: 'desc',
			headerClass: 'w-[7%]'
		},
		{
			// The one security identifier that means the same thing at every custodian. It has been
			// SEARCHABLE since the ledger was built and never shown, so the thing you could find was
			// the thing you could not read off the row.
			key: 'isin',
			header: 'ISIN',
			sortable: true,
			hideBelowWidth: '7xl',
			headerClass: 'w-[7%]',
			cellClass: 'truncate',
			sortValue: (t) => t.isin ?? ''
		},
		{
			// Almost always the account's own, and that is exactly why it is worth stating where it
			// is not: an unlabelled figure is only safe on a book with one currency.
			key: 'currency',
			header: 'Currency',
			sortable: true,
			hideBelowWidth: '7xl',
			headerClass: 'w-[6%]',
			sortValue: (t) => t.currency ?? ''
		},
		{
			key: 'amount',
			header: 'Amount',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			headerClass: 'w-[9%]',
			sortValue: (t) => t.amount
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[9%]'
		}
	]);

	// Server mode: ordered by the API. Local mode: ordered here, because passing `onSort` puts
	// `DataTable` in server-sorted mode and it no longer orders the rows itself.
	const displayRows = $derived(server ? rows : sortRows(rows, columns, localSort));

	/**
	 * The columns actually rendered. Dropping Client hands its 12% to Account rather than leaving the
	 * shares short of 100 — under `table-fixed` a short total gives the remainder to the last column,
	 * which is how a dropped column turns into an unevenly spaced table. Account is where it belongs:
	 * on one client's section the account name is the thing that tells their movements apart.
	 */
	/**
	 * The visible columns, with their width shares RENORMALISED to total 100.
	 *
	 * Under `table-fixed` the shares are the layout: short of 100 and the browser hands the
	 * remainder to the last column, over and the table overflows. Every mount here shows a
	 * different subset — a client's ledger drops Account, an account's drops both it and Client —
	 * so no set of hand-written
	 * percentages can add up in all of them. This used to be patched by widening one column to a
	 * magic 30% when Client was dropped, which held for exactly the two configurations that
	 * existed then and broke the moment a third column became optional.
	 *
	 * Scaling instead makes the invariant structural: the declared numbers are RATIOS, and whatever
	 * survives the filter is stretched to fill the row. The rounding remainder goes to the widest
	 * column, where a percentage point is least visible.
	 */
	const visibleColumns = $derived.by(() => {
		const dropped = new Set<string>();
		if (!showClient) dropped.add('client');
		if (!showAccount) dropped.add('account');
		const kept = columns.filter((c) => !dropped.has(c.key));

		const share = (c: (typeof columns)[number]) =>
			Number(/w-\[(\d+)%\]/.exec(c.headerClass ?? '')?.[1] ?? 0);
		const total = kept.reduce((sum, c) => sum + share(c), 0);
		if (!total) return kept;

		const scaled = kept.map((c) => ({
			column: c,
			width: Math.max(1, Math.round((share(c) * 100) / total))
		}));
		// Rounding twelve numbers rarely lands on 100 exactly; give the difference to the widest.
		const drift = 100 - scaled.reduce((sum, x) => sum + x.width, 0);
		if (drift !== 0) {
			const widest = scaled.reduce((a, b) => (b.width > a.width ? b : a));
			widest.width += drift;
		}
		// An INLINE STYLE, not a rewritten `w-[n%]` class. Tailwind only emits a rule for a class it
		// can find in the source, and `w-[16%]` — what an account's ledger computes for Type — is
		// written nowhere. The class rendered, matched no rule, and `table-fixed` fell back to an
		// even share: the exact layout this renormalisation exists to produce, silently not applied.
		// Every other mount happened to land on a percentage that some other file already used.
		//
		// The declared `w-[n%]` is stripped so the two cannot both be in play.
		return scaled.map(({ column, width }) => ({
			...column,
			headerClass: (column.headerClass ?? '').replace(/w-\[\d+%\]/, '').trim() || undefined,
			headerStyle: `width:${width}%`
		}));
	});

	/**
	 * The classes that show a folded column's value on its stand-in line, exactly while the column
	 * is hidden (`visibleWhenHidden`: the classes `DataTable` hides it with, read the other way).
	 * Undefined when the column never folds or is not on this mount, so nothing renders.
	 */
	const col = (key: string) => visibleColumns.find((c) => c.key === key);
	const accountFolded = $derived(visibleWhenHidden(col('account')));
	const assetFolded = $derived(visibleWhenHidden(col('asset')));
	const typeFolded = $derived(visibleWhenHidden(col('type')));
	const unitsFolded = $derived(visibleWhenHidden(col('quantity')));
	const currencyFolded = $derived(visibleWhenHidden(col('currency')));
	/** The reference takes the client's second line from 64rem down to 56rem, then gives it to the
	 *  account (the drawer and the panel show it). With no account to fold in, it keeps the line. */
	const referenceFolded = $derived(
		!col('uid')
			? undefined
			: accountFolded
				? 'hidden @4xl:block @5xl:hidden'
				: visibleWhenHidden(col('uid'))
	);
	/** The ISIN takes the asset's second line from 80rem down to 64rem, then gives it to the units. */
	const isinFolded = $derived(col('isin') ? 'hidden @5xl:block @7xl:hidden' : undefined);

	/**
	 * The row the drawer is showing, or null. A movement's row is nine truncated cells; the drawer
	 * is where the rest of it lives — the reference in full, the custodian's own word for it, the
	 * ISIN, the method — without spending a column on each.
	 *
	 * A UID with the row RE-DERIVED from `transactions`, not a copy of the row itself: the rows are
	 * replaced wholesale on every filter change and every fetch, and a held copy would go on showing
	 * a movement's old figures after a re-read revised them. Re-deriving also closes the drawer on
	 * its own when the row leaves the page, rather than stranding a panel about something no longer
	 * in the list.
	 */
	let selectedUid = $state<string | null>(null);
	const selected = $derived(
		selectedUid == null ? null : (transactions.find((t) => t.uid === selectedUid) ?? null)
	);

	// ---- The pushed panel (`detail="panel"` only) ---------------------------------------------

	let panelRow = $state<HTMLElement | null>(null);

	function close(): void {
		selectedUid = null;
	}

	// Opening the panel narrows the table and folds its columns, so the row that was clicked can
	// reflow out of the scrolled window. Bring it back once layout has settled.
	$effect(() => {
		if (!panel || !selectedUid) return;
		void tick().then(() =>
			requestAnimationFrame(() =>
				panelRow?.querySelector('tr.transaction-selected')?.scrollIntoView({ block: 'nearest' })
			)
		);
	});

	/** Esc closes the panel, unless it belongs to something else first: a popover, menu or dialog
	 *  layered on top, or a field being typed in (the search box sits right above the rows). The
	 *  drawer handles its own Esc, so this does nothing there. */
	function onkeydown(event: KeyboardEvent): void {
		if (!panel || event.key !== 'Escape' || !selectedUid || event.defaultPrevented) return;
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
		if (
			document.querySelector(
				'[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"]'
			)
		) {
			return;
		}
		close();
	}
</script>

<svelte:window {onkeydown} />

<!-- ONE SCREEN. The page is a flex column the height of the viewport under the top bar, and the
     LIST is the only thing that scrolls — so the heading, the toolbar and the pager stay put and
     reachable however long the book is, instead of the pager sitting a thousand rows below the fold.
     `-m-4 md:-m-6` cancels the shell's own canvas padding and re-applies it inside, because a page
     that owns the full height cannot also be inset by its parent; `min-h-0` on the card is what lets
     a flex child shrink below its content, without which the table just grows and nothing scrolls. -->
<div
	class={embedded
		? fills
			? 'flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden'
			: 'flex flex-col gap-4'
		: '-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6'}
>
	<!-- The description says what the page HOLDS. "{n} transactions across the firm" counted the rows
	     currently shown, so on a paged listing it read the page size rather than the firm — 25, then
	     25, then 25 — which is worse than no number at all. -->
	{@render actionRequired?.(outstanding)}

	<!-- The card claims the remaining height and scrolls its rows ONLY where it owns the screen. On a
	     stacked record three bands share one page, so a band that takes `flex-1` starves its
	     siblings and gives the reader two scrollbars. -->
	{#snippet clearFilters()}
		<Button variant="outline" size="sm" onclick={clear}>Clear filters</Button>
	{/snippet}

	{#snippet nothingToShow()}
		<!-- The action is passed only when there IS one: `EmptyState` renders the action's box on the
	     snippet being present, so an always-passed snippet that renders nothing leaves an empty
	     16px gap under the text. A filtered miss has a way out; an empty ledger does not. -->
		<EmptyState
			compact
			icon={ArrowLeftRightIcon}
			title={emptyTitle}
			description={emptyDescription}
			action={dirty || term ? clearFilters : undefined}
		/>
	{/snippet}

	{#snippet listingCard()}
		<Card
			glass
			class="{fills ? 'flex min-w-0 flex-col md:min-h-0 md:flex-1' : 'flex flex-col'} {minHeight}"
		>
			<CardContent
				class={fills
					? 'flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden'
					: 'flex flex-col gap-4'}
			>
				<ListingToolbar
					class="shrink-0"
					query={term}
					placeholder={searchPlaceholder}
					{dirty}
					onclear={clear}
					onsearch={listing && onquery
						? (next) => onquery({ ...listing, q: next, offset: 0 })
						: (next) => (localQuery = next)}
					{filterDefs}
					{filterValue}
					onfilters={applyFilters}
				>
					{#snippet filters()}
						{#if !server}
							<FilterSelect
								bind:value={statusFilter}
								options={statusOptions}
								allLabel="All statuses"
								label="Filter by status"
							/>
							{#if showAdviserFilter}
								<FilterSelect
									bind:value={adviserFilter}
									options={adviserOptions}
									allLabel="All advisers"
									label="Filter by adviser"
								/>
							{/if}
							<FilterSelect
								bind:value={typeFilter}
								options={typeOptions}
								allLabel="All types"
								label="Filter by type"
							/>
						{/if}
					{/snippet}
				</ListingToolbar>

				<!-- Selecting a row opens the movement in the drawer or the panel. `onRowClick` and not an
				     anchor per row: a transaction has no page of its own, and `DataTable` makes the row
				     keyboard-reachable (Enter/Space) and steps aside for the links inside it, so clicking
				     the account or the client still navigates rather than opening the detail over the row
				     you aimed at. In the panel the selected row stays tinted, with the sidebar's active
				     rule down its leading edge (the Contributions work queue's own classes);
				     `transaction-selected` carries no style, it is how the panel finds the row to scroll
				     back into view. -->
				<DataTable
					columns={visibleColumns}
					rows={displayRows}
					{sort}
					onSort={chooseSort}
					scroll={fills}
					fixed
					getKey={(t) => t.uid}
					onRowClick={(t) => (selectedUid = t.uid)}
					rowClass={panel
						? (t) =>
								t.uid === selectedUid
									? 'transaction-selected bg-accent/60 [&>td:first-child]:shadow-[inset_2px_0_0_0_var(--primary)]'
									: undefined
						: undefined}
					label="Transactions"
					empty={nothingToShow}
				>
					{#snippet cell(t, column)}
						{#if column.key === 'date'}
							<span class="whitespace-nowrap tabular-nums">{formatDate(t.date)}</span>
						{:else if column.key === 'uid'}
							<!-- The reference, back on the row. It is the whole reason this listing exists rather
							     than a per-account one: you come here with a reference from a statement or a client
							     and look for the movement it names. `TruncatedText` so a long one reveals in full on
							     hover instead of being clipped with no way to read it. -->
							<TruncatedText
								text={transactionReference(t.uid)}
								class="text-muted-foreground max-w-full font-mono text-xs"
							/>
						{:else if column.key === 'account'}
							<span class="block min-w-0">
								<TruncatedText
									text={t.account_name}
									href={links.account(t.account_uid)}
									class="max-w-full"
								/>
								<RecordId value={providerReference(t.account_uid)} class="max-w-full" />
							</span>
						{:else if column.key === 'client'}
							<!-- `items-start` keeps each line as wide as its text, so a click beside a name
							     selects the row rather than following the link; `max-w-full` caps each at the
							     column, so a long name cuts off instead of printing over the next one. -->
							<span class="flex w-full min-w-0 flex-col items-start">
								<TruncatedText
									text={t.client_name}
									href={t.client_uid ? links.client(t.client_uid) : undefined}
									class="max-w-full"
								/>
								{#if accountFolded}
									<!-- Under 56rem the Account column folds and the account takes this line. -->
									<TruncatedText
										text={t.account_name}
										href={links.account(t.account_uid)}
										class="text-muted-foreground max-w-full text-[11px] {accountFolded}"
									/>
								{/if}
								{#if referenceFolded}
									<!-- 56–64rem: Reference has folded but Account has not, so the reference keeps
									     its place on the row here, as the work queue keeps a contribution's. -->
									<span class="max-w-full {referenceFolded}">
										<RecordId label="Ref" value={transactionReference(t.uid)} class="max-w-full" />
									</span>
								{/if}
							</span>
						{:else if column.key === 'type'}
							<span class="flex min-w-0 flex-col">
								<span class="flex min-w-0 items-center gap-1.5">
									<TruncatedText
										text={`${humanise(t.type)}${subtypeDetail(t) ? `: ${subtypeDetail(t)}` : ''}`}
										class="max-w-full"
									/>
									{#if t.category}
										<Badge variant="outline" class="shrink-0 font-normal"
											>{humanise(t.category)}</Badge
										>
									{/if}
								</span>
								{#if assetFolded}
									<!-- Under 56rem the Asset column folds and the asset takes this line. Cash says so. -->
									<TruncatedText
										text={t.asset ?? 'Cash'}
										class="text-muted-foreground max-w-full text-[11px] {assetFolded}"
									/>
								{/if}
							</span>
						{:else if column.key === 'amount'}
							<!-- Deliberately NEITHER `signed` NOR `tone="directional"`. `platform.transactions`
							     stores every amount UNSIGNED: the conformed layer reads the custodian's
							     direction into `type` and then publishes the figure without it, so a
							     withdrawal and a deposit arrive as the same positive number. Rendering a sign
							     off that put a `+` on every row and coloured the lot green — a withdrawal
							     shown as money in, which is worse than showing no direction at all. The Type
							     column is what says which way the money went until the direction is carried
							     in the figure itself.
							     A foreign currency gets a second, muted line: the same figure in the account
							     currency, at the custodian's own rate. -->
							{@const caption = conversionCaption(t)}
							{@const code = currencyFolded ? t.currency : null}
							<span class="flex min-w-0 flex-col items-end">
								<Money amount={t.amount} currency={t.currency} class="font-medium" />
								{#if typeFolded || code}
									<!-- The stand-in line. Under 80rem the Currency column folds and its code sits
									     here, with the flag the column's pill leads with; under 42rem Type folds too,
									     and the figure is unsigned, so the one word that says which way it moved
									     comes first. The subtype is in the detail. Type only ever folds once Currency
									     has, so the dot is the type's to carry. -->
									<span
										class="text-muted-foreground flex max-w-full min-w-0 justify-end gap-1 text-[11px] leading-tight font-normal"
									>
										{#if typeFolded}
											<span class="min-w-0 truncate {typeFolded}"
												>{humanise(t.type)}{code ? ' ·' : ''}</span
											>
										{/if}
										{#if code}
											<span class="shrink-0 {currencyFolded}"
												><span class="inline-flex items-center gap-1"
													><CurrencyFlag {code} class="size-2.5" />{code}</span
												></span
											>
										{/if}
									</span>
								{/if}
								{#if caption}
									<span class="text-muted-foreground text-[11px] leading-tight font-normal">
										<Money
											amount={caption.amount}
											currency={caption.currency}
											class="font-normal"
										/>
									</span>
								{/if}
							</span>
						{:else if column.key === 'asset'}
							<!-- The asset, and the identifiers that fold into it: the ISIN from 80rem down to
							     64rem, then the units (a cash movement has neither, so its row stays one line). -->
							<span class="flex min-w-0 flex-col">
								<TruncatedText text={t.asset ?? 'Cash'} class="text-muted-foreground max-w-full" />
								{#if isinFolded && t.isin}
									<span class="text-muted-foreground truncate font-mono text-[11px] {isinFolded}"
										>{t.isin}</span
									>
								{/if}
								{#if unitsFolded && t.quantity != null}
									<span
										class="text-muted-foreground truncate text-[11px] tabular-nums {unitsFolded}"
										>{t.quantity.toLocaleString('en-GB')} units</span
									>
								{/if}
							</span>
						{:else if column.key === 'quantity'}
							<span class="tabular-nums">
								{t.quantity == null ? '—' : t.quantity.toLocaleString('en-GB')}
							</span>
						{:else if column.key === 'isin'}
							<!-- Monospace, because an ISIN is read character by character when it is checked
							     against a custodian's statement, not skimmed as a word. The ABSENCE of one is
							     not an identifier though, so the placeholder stays out of the mono/xs
							     treatment: rendered as a monospace 12px glyph the em dash came out visibly
							     shorter and lighter than the identical dash in Units beside it, and the two
							     columns are empty on the same rows (a cash movement has neither), so the
							     mismatch was on show down the whole table. -->
							{#if t.isin}
								<TruncatedText text={t.isin} class="text-muted-foreground font-mono text-xs" />
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						{:else if column.key === 'currency'}
							{#if t.currency}
								<CurrencyBadge code={t.currency} />
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						{:else if column.key === 'status'}
							<!-- Capped at the column, as on Accounts: a long word cuts off with the whole of it on hover, rather
							     than printing over the next column (FB-20260921-09). -->
							<StatusBadge tone={statusTone(t.status)} class="max-w-full min-w-0"
								><TruncatedText text={statusWord(t.status)} class="min-w-0" /></StatusBadge
							>
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
	{/snippet}

	{#if panel}
		<!-- THE PANEL OPENS ON DEMAND, as on the Contributions workbench. With nothing selected the
		     table has the whole row; selecting one PUSHES the panel in beside it. Push, not a drawer: a
		     drawer covers the rows, and reading down a ledger you lose your place on every one. The
		     table gives up exactly the panel's width and folds its own columns to fit. Below `xl`
		     there is no room for both, so the panel takes the table's place until it is closed; the
		     table stays mounted under it, so its filters and page survive. -->
		<div
			bind:this={panelRow}
			class={fills
				? 'flex min-w-0 gap-4 md:min-h-0 md:flex-1 md:overflow-hidden'
				: 'flex min-w-0 gap-4'}
		>
			<div
				class={cn(
					fills ? 'flex min-w-0 flex-1 flex-col md:min-h-0' : 'flex min-w-0 flex-1 flex-col',
					selected && 'max-xl:hidden'
				)}
			>
				{@render listingCard()}
			</div>
			{#if selected}
				<!-- A flex sibling of a fixed width, so the table gives up exactly this much. No backdrop
				     and no focus trap, so the list stays readable and the next row is one click away. The
				     panel scrolls inside itself, so a long detail cannot push the table off-screen. It
				     slides in from the edge it sits on (`panel-motion.ts`); picking another row brings in
				     only its contents (`motion-item-enter`). -->
				<aside
					aria-label="Selected transaction"
					class="flex min-h-0 min-w-0 flex-1 flex-col xl:w-[380px] xl:flex-none 2xl:w-[420px]"
					in:panelSlide
					out:panelSlide
				>
					<div class="border-border/70 bg-card flex h-full min-h-0 flex-col rounded-xl border">
						{#key selected.uid}
							<div class="motion-item-enter flex min-h-0 flex-1 flex-col">
								<div
									class="border-border/70 flex items-start justify-between gap-2 border-b py-2.5 pr-2 pl-4"
								>
									<div class="min-w-0 pt-1">
										<h2 class="truncate font-semibold">
											{humanise(selected.type)}{subtypeDetail(selected)
												? `: ${subtypeDetail(selected)}`
												: ''}
										</h2>
										<p class="text-muted-foreground truncate text-sm">
											{selected.account_name} · {formatDate(selected.date)}
										</p>
									</div>
									<Button
										variant="ghost"
										size="icon"
										class="text-muted-foreground hover:text-foreground size-8 shrink-0"
										aria-label="Close panel"
										title="Close (Esc)"
										onclick={close}
									>
										<XIcon class="size-4" />
									</Button>
								</div>
								<div class="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
									{@render detailBody(selected)}
								</div>
							</div>
						{/key}
					</div>
				</aside>
			{/if}
		</div>
	{:else}
		{@render listingCard()}
	{/if}
</div>

<!-- THE DRAWER, for every mount that keeps the default: a client's ledger, an account's band and the
     admin book's read-only copy all read the same movement the same way, over the page. -->
{#if !panel}
	<Sheet.Root
		open={selected != null}
		onOpenChange={(open) => {
			if (!open) selectedUid = null;
		}}
	>
		<Sheet.Content side="right" class="gap-0 sm:!w-[26rem]">
			{#if selected}
				<Sheet.Header>
					<Sheet.Title>
						{humanise(selected.type)}{subtypeDetail(selected) ? `: ${subtypeDetail(selected)}` : ''}
					</Sheet.Title>
					<Sheet.Description>
						{selected.account_name} · {formatDate(selected.date)}
					</Sheet.Description>
				</Sheet.Header>
				<div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pb-4">
					{@render detailBody(selected)}
				</div>
			{/if}
		</Sheet.Content>
	</Sheet.Root>
{/if}

<!-- The movement's detail, shared by the drawer and the panel above, so both show the same thing. Its
     fields are built by `transaction-detail.ts`, so what is shown — and what is left out for a cash
     movement — is decided once and unit-tested. -->
{#snippet detailBody(t: Transaction)}
	{@const caption = conversionCaption(t)}
	<!-- The amount and the state lead, because they are what the row was opened to confirm.
	     `Money` and a `StatusBadge`, not text — the same treatment the column gives them,
	     and for the same reason the ledger's Amount column carries no sign: the conformed
	     feed publishes every figure unsigned and the type is what says which way it went. -->
	<div class="flex items-start justify-between gap-4">
		<p class="text-xl font-semibold tracking-tight">
			<Money amount={t.amount} currency={t.currency} />
			{#if caption}
				<span class="text-muted-foreground block text-sm font-normal">
					<Money amount={caption.amount} currency={caption.currency} class="font-normal" />
				</span>
			{/if}
		</p>
		<StatusBadge tone={statusTone(t.status)}>{statusWord(t.status)}</StatusBadge>
	</div>

	{#if t.needs_categorisation && !t.category}
		<p class="bg-brand-subtle rounded-lg px-3 py-2 text-sm">
			This payment into a pension has not been categorised yet. Relief at source depends on whether
			it is a personal or an employer contribution.
		</p>
	{/if}

	<!-- The account and the client are LINKS, so the drawer is a way into the record rather
	     than a dead end. Rendered here and not as detail fields for exactly that reason.
	     The rail is 26rem, so the value takes the width the label does not: a percentage
	     cap was for the 58vw console, where one long name otherwise crossed the panel. -->
	<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 border-t pt-3 text-xs">
		<dt class="text-muted-foreground">Account</dt>
		<dd class="min-w-0">
			<a href={links.account(t.account_uid)} class={LINK_RECORD}>
				{t.account_name}
			</a>
		</dd>
		<dt class="text-muted-foreground">Client</dt>
		<dd class="min-w-0">
			{#if t.client_uid}
				<a href={links.client(t.client_uid)} class={LINK_RECORD}>
					{t.client_name}
				</a>
			{:else}
				{t.client_name}
			{/if}
		</dd>
	</dl>

	{#each transactionDetailGroups(t) as group (group.title)}
		<div class="border-t pt-3">
			<h4 class="text-muted-foreground mb-1.5 text-xs font-semibold">{group.title}</h4>
			<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
				{#each group.fields as field (field.label)}
					<dt class="text-muted-foreground">{field.label}</dt>
					<dd class={field.mono ? 'min-w-0 font-mono' : 'min-w-0'}>
						{#if field.money}
							<Money amount={field.money.amount} currency={field.money.currency} />
						{:else if field.currency}
							<span class="inline-flex items-center gap-1.5"
								><CurrencyFlag code={field.currency} />{field.value}</span
							>
						{:else if field.person}
							<span class="inline-flex min-w-0 items-center gap-1.5"
								><PersonAvatar name={field.person} size="xs" class="-my-0.5" /><span
									class="truncate">{field.value}</span
								></span
							>
						{:else}
							{field.value}
						{/if}
					</dd>
				{/each}
			</dl>
		</div>
	{/each}
{/snippet}
