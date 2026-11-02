<script lang="ts">
	// The firm's book as one table: search + status/adviser/type filters + the sortable DataTable.
	//
	// Shared on purpose. The dashboard renders the real book here rather than a "top clients"
	// teaser, and `/clients` can render the same component for its live and archived segments, so the
	// two screens cannot drift in columns, search fields, sort defaults or empty-state wording.
	// The pure half (columns, derived status, filter predicate) is `./book.ts` and is unit-tested.
	//
	// What it deliberately does NOT own: the population, or the search/filter state. The caller
	// chooses which rows go in (`/clients`: live vs archived) and
	// holds `criteria`, so its heading can count exactly what the table shows by running the same
	// `filterBook`. An earlier cut had the component hand its filtered rows back up out of an
	// `$effect`; effects don't run during SSR, so every first paint stated "0 of 14" above a table
	// of 14 and only corrected itself on hydration.
	import AdviserStack from '../../data/adviser-stack.svelte';
	import AccountTags from '../clients/account-tags.svelte';
	import { applyHoldings, type ContactHoldings } from '../clients/holdings.js';
	import { watchHoldings } from '../clients/holdings.svelte.js';
	import { clientName, clientSubline } from '../clients/format.js';
	import {
		DataTable,
		defaultSort,
		sortRows,
		visibleWhenHidden,
		type DataTableColumn,
		type DataTableSort
	} from '../../ui/data-table/index.js';
	import { CurrencyBadge } from '../../ui/currency-badge/index.js';
	import { Money } from '../../ui/money/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import type { Snippet } from 'svelte';
	import ClientAvatar from '../../data/client-avatar.svelte';
	import { clientKind } from '../../data/client-kind.js';
	import {
		ALL,
		type FilterDef,
		barValueFrom,
		withBarValue,
		ListingToolbar,
		PAGE_SIZE,
		PaginationStrip,
		humanise,
		isFiltered,
		optionsFrom,
		optionsFromFacet,
		rangeFromBounds,
		pageOf,
		rangeLabel,
		statusTone,
		type ListingQuery
	} from '../listing/index.js';
	import {
		BOOK_COLUMNS,
		STATUS_OPTIONS,
		filterBook,
		statusOf,
		type BookFilters,
		type BookRow
	} from './book';

	let {
		rows,
		empty,
		criteria = $bindable(),
		archived = false,
		sort = $bindable(null),
		placeholder = 'Search clients, reference or email',
		extraColumns = [],
		extraCell,
		extraFilters,
		scroll = false,
		listing = null,
		matchCount = null,
		facets,
		bounds,
		onquery,
		pageHref,
		clientHref = (uid: string) => `/clients/${uid}`,
		loading = false,
		holdings = null
	}: {
		/** The population to show — already narrowed to a segment by the caller. */
		rows: BookRow[];
		/** What the table shows when it has no rows. A SNIPPET as well as a string, because there are
		 *  FOUR of these — no clients, no matches, not permitted, failed to read — and two of them
		 *  are failures that must not wear the neutral treatment. `DataTable.empty` takes both. */
		empty: string | Snippet;
		/** Search + filter state, owned by the caller (see the note above). */
		criteria: BookFilters;
		/** Closed rows: Status reads Closed and the live-book status filter is hidden (every row
		 *  here is Closed). Names stay links — the record itself is still loadable. */
		archived?: boolean;
		/** NULL: falls back to the `sortDefault` column (Client) — see `defaultSort`. */
		sort?: DataTableSort | null;
		placeholder?: string;
		/** Trailing columns the caller renders itself (e.g. `/clients`' Reopen control). */
		extraColumns?: DataTableColumn<BookRow>[];
		extraCell?: Snippet<[BookRow, DataTableColumn<BookRow>]>;
		/** Filters the caller adds to this toolbar. A caller's own filter belongs HERE rather than in
		 *  a control above the table, so it narrows the same population the filters beside it narrow
		 *  and there is one place to look for "what is hiding rows". Nothing passes it today. */
		extraFilters?: Snippet;
		/** Scroll the ROWS, not the page — for a dashboard or listing that fits one screen. Needs a
		 *  bounded height from the caller. */
		scroll?: boolean;
		listing?: ListingQuery | null;
		matchCount?: number | null;
		facets?: Record<string, string[]>;
		/** The slider track — see `ListingPage.bounds`. */
		bounds?: Record<string, number[]>;
		onquery?: (next: ListingQuery) => void;
		pageHref?: (page: number) => string;
		/** Where a client's name links — injected for admin's operate-as-firm mount. */
		clientHref?: (uid: string) => string;
		/** The rows on screen are the PREVIOUS page while the next one is fetched (`Streamed`'s
		 *  `stale`). The table draws skeleton rows in their place; the toolbar and the pager stay. */
		loading?: boolean;
		/** Optional later fill. The row already carries the stored valuation. */
		holdings?: Promise<ContactHoldings | null> | null;
	} = $props();

	const server = $derived(!!listing && !!onquery);

	const adviserOptions = $derived(
		server ? optionsFromFacet(facets, 'adviser') : optionsFrom(rows, (c) => c.advisers)
	);
	const typeOptions = $derived(
		server ? optionsFromFacet(facets, 'type', humanise) : optionsFrom(rows, (c) => c.type, humanise)
	);

	/** The status filter belongs to the LIVE book: every closed row reads as Closed, so a status
	 *  carried into the archived segment would silently empty a list nobody can see is filtered. */
	const BAR_KEYS = $derived(
		archived ? ['adviser', 'type', 'value'] : ['status', 'adviser', 'type', 'value']
	);
	/** Held as `[from, to]`, so it must not go through the set-cleaning the others do — dropping a
	 *  blank turns "up to 3,000" into "from 3,000". See `barValueFrom`'s `rangeKeys`. */
	const RANGE_KEYS: readonly string[] = ['value'];

	const localValueSpan = $derived.by(() => {
		const seen = rows
			.filter((c) => c.valued_account_count > 0)
			.map((c) => c.portfolio_value)
			.filter((v) => Number.isFinite(v));
		return seen.length ? [Math.min(...seen), Math.max(...seen)] : [];
	});

	const filterDefs = $derived<FilterDef[]>([
		...(archived ? [] : [{ key: 'status', label: 'Status', options: STATUS_OPTIONS }]),
		{
			key: 'adviser',
			label: 'Adviser',
			options: adviserOptions,
			searchPlaceholder: 'Search advisers…'
		},
		{ key: 'type', label: 'Type', options: typeOptions },
		{
			key: 'value',
			label: 'Portfolio value',
			options: [],
			kind: 'numberrange',
			currency: 'GBP',
			...(server
				? (rangeFromBounds(bounds, 'value') ?? {})
				: (rangeFromBounds({ value: localValueSpan }, 'value') ?? {}))
		}
	]);

	/** The bar's shape in LOCAL mode, read off `criteria` — which the caller owns and counts with. */
	const localBar = $derived.by(() => {
		const out: Record<string, string[]> = {};
		for (const key of BAR_KEYS) {
			if (RANGE_KEYS.includes(key)) {
				const pair = criteria.value ?? [];
				if (pair[0] || pair[1]) out[key] = [pair[0] ?? '', pair[1] ?? ''];
				continue;
			}
			const held = criteria[key as 'status' | 'adviser' | 'type'];
			const values = (Array.isArray(held) ? held : [held]).filter((v) => v && v !== ALL);
			if (values.length) out[key] = values;
		}
		return out;
	});
	const barValue = $derived(server ? barValueFrom(listing, BAR_KEYS, RANGE_KEYS) : localBar);

	function setBar(next: Record<string, string[]>): void {
		if (server && listing && onquery) {
			onquery(withBarValue(listing, BAR_KEYS, next, RANGE_KEYS));
			return;
		}
		// `ALL` for an unset filter, because `filterBook` and the caller's own count both read
		// `criteria` and an empty array would have to mean the same thing in three places.
		criteria.status = next.status ?? ALL;
		criteria.adviser = next.adviser ?? ALL;
		criteria.type = next.type ?? ALL;
		criteria.value = next.value?.length ? next.value : undefined;
	}

	const dirty = $derived(server ? isFiltered(listing!) : Object.keys(localBar).length > 0);
	function clear() {
		if (server && listing && onquery) {
			onquery({ ...listing, q: '', filters: {}, offset: 0 });
			return;
		}
		criteria.value = undefined;
		criteria.status = ALL;
		criteria.adviser = ALL;
		criteria.type = ALL;
	}

	const watched = watchHoldings(() => holdings);
	const shown = $derived(applyHoldings(rows, watched.current));
	const filtered = $derived(server ? shown : filterBook(shown, criteria, { archived }));
	const columns = $derived<DataTableColumn<BookRow>[]>([...BOOK_COLUMNS, ...extraColumns]);

	const PER_PAGE = PAGE_SIZE;
	// Server-driven: the page IS the URL's offset, so the pager's marker moves with the rows it
	// labels. (It was a plain `$state(1)` for both modes, and in server mode nothing ever wrote to
	// it — the label read "26–50 of 798" while page 1 stayed highlighted.) Locally paged: a
	// writable derived, so `bind:page` from the strip still owns it.
	let page = $derived(server && listing ? pageOf(listing) : 1);
	const pageCount = $derived(
		server
			? Math.max(1, Math.ceil((matchCount ?? 0) / (listing?.limit ?? PER_PAGE)))
			: Math.max(1, Math.ceil(filtered.length / PER_PAGE))
	);
	$effect(() => {
		if (!server && page > pageCount) page = pageCount;
	});
	const tableSort = $derived<DataTableSort | null>(
		server && listing
			? listing.sort
				? { key: listing.sort, dir: listing.dir }
				: null
			: (sort ?? defaultSort(columns))
	);

	/** A header click goes to the server when there is one — sorting the local page would reorder
	 *  25 rows and call it "by value" while the real largest client sits pages away. */
	function chooseSort(next: DataTableSort | null): void {
		if (!server || !listing || !onquery) {
			sort = next;
			return;
		}
		onquery({ ...listing, sort: next?.key ?? null, dir: next?.dir ?? 'asc', offset: 0 });
	}

	const sorted = $derived(server ? filtered : sortRows(filtered, columns, tableSort));
	const paged = $derived(server ? sorted : sorted.slice((page - 1) * PER_PAGE, page * PER_PAGE));
	const shownLabel = $derived(
		server && listing
			? rangeLabel(listing, matchCount ?? 0, paged.length)
			: filtered.length === 0
				? 'No matches'
				: filtered.length <= PER_PAGE
					? `${filtered.length} ${filtered.length === 1 ? 'client' : 'clients'}`
					: `${(page - 1) * PER_PAGE + 1}–${Math.min(page * PER_PAGE, filtered.length)} of ${filtered.length}`
	);
</script>

<div
	class={scroll
		? 'flex min-w-0 flex-col gap-3 md:min-h-0 md:flex-1 md:overflow-hidden'
		: 'space-y-3'}
>
	<ListingToolbar
		query={server ? (listing?.q ?? '') : criteria.query}
		{placeholder}
		{dirty}
		onclear={clear}
		onsearch={server && listing && onquery
			? (next) => onquery({ ...listing, q: next, offset: 0 })
			: (next) => (criteria.query = next)}
		class={scroll ? 'shrink-0' : undefined}
		{filterDefs}
		filterValue={barValue}
		onfilters={setBar}
	>
		{#snippet filters()}
			{@render extraFilters?.()}
		{/snippet}
	</ListingToolbar>

	<DataTable
		{columns}
		rows={paged}
		sort={tableSort}
		onSort={chooseSort}
		{scroll}
		fixed
		getKey={(c) => c.id}
		{empty}
		{loading}
	>
		{#snippet cell(c, column)}
			{#if column.key === 'name'}
				<!-- The /clients listing's name cell: this IS that table, so it must not read differently.
				     The name, then the date of birth under it — or, for an organisation, where it is. -->
				{@const subline = clientSubline(c)}
				<span class="flex min-w-0 items-center gap-2.5">
					<ClientAvatar name={clientName(c)} kind={clientKind(c)} size="sm" />
					<span class="block min-w-0">
						<TruncatedText text={clientName(c)} href={clientHref(c.uid)} class="font-medium" />
						{#if subline}
							<TruncatedText text={subline} class="text-muted-foreground text-xs" />
						{/if}
					</span>
				</span>
			{:else if column.key === 'accounts'}
				<AccountTags wrappers={c.account_types ?? []} count={c.account_count} />
			{:else if column.key === 'adviser'}
				<AdviserStack names={c.advisers} />
			{:else if column.key === 'currency'}
				{#if c.currency}
					<CurrencyBadge code={c.currency} />
				{:else}
					<span class="text-muted-foreground">—</span>
				{/if}
			{:else if column.key === 'value'}
				<!-- Growth under the value while its own column is hidden — see the /clients listing,
				     which this table must read the same as. -->
				{@const underValue = visibleWhenHidden(columns.find((col) => col.key === 'growth'))}
				<Money amount={c.portfolio_value} currency={c.currency || 'GBP'} class="font-medium" />
				{#if underValue}
					<Money
						amount={c.growth}
						currency={c.currency || 'GBP'}
						signed
						tone="directional"
						class={underValue}
					/>
				{/if}
			{:else if column.key === 'cash'}
				<Money amount={c.cash} currency={c.currency || 'GBP'} />
			{:else if column.key === 'growth'}
				<Money amount={c.growth} currency={c.currency || 'GBP'} signed tone="directional" />
			{:else if column.key === 'status'}
				{#if archived}
					<StatusBadge tone={statusTone('closed')}>Closed</StatusBadge>
				{:else}
					{@const s = statusOf(c)}
					<StatusBadge tone={statusTone(s)}>{humanise(s)}</StatusBadge>
				{/if}
			{:else if extraCell}
				{@render extraCell(c, column)}
			{/if}
		{/snippet}
	</DataTable>

	<PaginationStrip
		label={shownLabel}
		bind:page
		{pageCount}
		href={server && pageHref ? pageHref : undefined}
	/>
</div>
