<script lang="ts">
	// The clients listing — the platform portal's /clients page, extracted so the admin portal can
	// render the same tenant book (the portal waterfall: ops are a proxy middle-office).
	//
	// Data comes in as typed props (the app's loader fetches it), every href is built by the
	// injected `links`, and mutations only exist when the app passes `actions` — the admin wave
	// may pass none and gets a read-only book. Create is a SEPARATE name from restore so a
	// permission that can reopen a closed client is not forced to mint a new one (and so a
	// read-only mount never grows a New client button by accident). `contacts:create` is that
	// mint permission; `contacts:write` is reopen/edit.
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { CurrencyBadge } from '../../ui/currency-badge/index.js';
	import AccountTags from './account-tags.svelte';
	import { applyHoldings, type ContactHoldings } from './holdings.js';
	import { watchHoldings } from './holdings.svelte.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import EmailInput from '../../../forms/field-inputs/email-input.svelte';
	import PhoneNumberInput from '../../../forms/field-inputs/phone-number-input.svelte';
	import { emailIssue, phoneIssue } from '../../../forms/editor/masks.js';
	import { default as Segmented } from '../../ui/segmented/segmented.svelte';
	import * as Sheet from '../../ui/sheet/index.js';
	import {
		DataTable,
		defaultSort,
		sortRows,
		visibleWhenHidden,
		type DataTableColumn,
		type DataTableSort
	} from '../../ui/data-table/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import UsersIcon from '@lucide/svelte/icons/users';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import { Money } from '../../ui/money/index.js';
	import ExportMenu from '../listing/export-menu.svelte';
	import AdviserStack from '../../data/adviser-stack.svelte';
	import ClientAvatar from '../../data/client-avatar.svelte';
	import { clientKind } from '../../data/client-kind.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { statusTone } from '../listing/status-tone.js';
	import {
		ALL,
		type FilterDef,
		manyFilters,
		ListingToolbar,
		PAGE_SIZE,
		Pagination,
		PaginationStrip,
		humanise,
		isFiltered,
		matches,
		matchesAny,
		optionsFrom,
		optionsFromFacet,
		rangeFromBounds,
		withinRange,
		rangeLabelFromOffset,
		search,
		type ListingQuery
	} from '../listing/index.js';
	import { custodianLabel } from '../listing/custodian.js';
	import type { Enhance } from './form-enhance.js';
	import { clientName, clientSubline } from './format.js';
	import { clientTypeLabel, ORGANISATION_SUB_TYPES } from './client-type.js';
	import { clientStatus } from './status.js';
	import type { ClientListRow, ClientsListingLinks } from './types.js';

	/** The mutations this listing can host. Absent ⇒ read-only: no Reopen column, no New client.
	 *  The app owns its form-action names; this component only posts to them. Restore and create
	 *  are independent so a role that can mint is not forced to reopen, and the reverse. */
	export type ClientsListingActions = {
		/** SvelteKit's `enhance`, injected because pkgs/ui cannot import `$app/forms`. */
		enhance: Enhance;
		/** The restore form's action, e.g. `?/restoreClient`. Absent ⇒ no Reopen column. */
		restore?: string;
		/** The create form's action, e.g. `?/createClient`. Absent ⇒ no New client button — this is
		 *  how `contacts:create` (and a read-only admin mount) control the affordance. */
		create?: string;
		/** After a successful create, take the adviser to the new record. Injected because this
		 *  package cannot import `$app/navigation`; without it we fall back to a full navigation. */
		oncreated?: (uid: string) => void;
	};

	let {
		clients,
		archived = [],
		loadError = false,
		archivedError = false,
		links,
		actions = null,
		/** `?segment=closed` (or the legacy `archived`) deep link. */
		initialSegment = 'live',
		/** Re-runs the app's load (`invalidateAll`) behind the error banners' Retry. */
		onretry,
		listing = null,
		matchCount = null,
		facets,
		facetCounts,
		bounds,
		onquery,
		pageHref,
		exportHref,
		onsegment,
		/** `?new=1` deep link — open the create drawer on first paint. Ignored when `create` is
		 *  absent, so a URL cannot conjure a write the caller is not allowed. */
		initialCreate = false,
		holdings = null
	}: {
		clients: ClientListRow[];
		archived?: ClientListRow[];
		valuesAsOf?: string | null;
		loadError?: boolean;
		archivedError?: boolean;
		links: ClientsListingLinks;
		actions?: ClientsListingActions | null;
		initialSegment?: 'live' | 'archived';
		onretry?: () => void;
		/** SERVER-DRIVEN when supplied: `clients` is one page and the API applied the search and
		 *  filters. The ARCHIVED segment stays local — the API returns that population whole, and
		 *  it is a different list rather than a filter of this one. */
		listing?: ListingQuery | null;
		/** Matches across the whole book. Named `matchCount` because this listing already uses
		 *  `total` for summed portfolio value. */
		matchCount?: number | null;
		facets?: Record<string, string[]>;
		facetCounts?: Record<string, Record<string, number>>;
		bounds?: Record<string, number[]>;
		onquery?: (next: ListingQuery) => void;
		/** Builds the URL for a 1-indexed page, so the pager renders real links (shareable,
		 *  middle-clickable, back-button friendly). Injected because only the app knows its own
		 *  route and which unrelated params must survive — the same reason `links` is injected. */
		pageHref?: (page: number) => string;
		/** Export endpoint for the CURRENT filter set. Absent ⇒ no export menu. */
		exportHref?: string;
		/** When the Closed / live population switch fires — so the app can put `?segment=`
		 *  in the URL and only fetch closed clients when that segment is selected. */
		onsegment?: (segment: 'live' | 'archived') => void;
		initialCreate?: boolean;
		/** Optional later fill. The row already carries the stored valuation. */
		holdings?: Promise<ContactHoldings | null> | null;
	} = $props();

	// `use:` needs a plain identifier: alias the injected enhance (never called when read-only —
	// every form is inside an `{#if actions}` branch, so the noop fallback only satisfies types).
	const enhanceForm: Enhance = $derived(actions?.enhance ?? (() => {}));

	let localQuery = $state('');
	let statusFilter = $state<string[]>([]);
	let adviserFilter = $state<string[]>([]);
	let custodianFilter = $state<string[]>([]);
	let typeFilter = $state<string[]>([]);
	let accountTypeFilter = $state<string[]>([]);
	let valueFilter = $state<string[]>([]);
	let localSort = $state<DataTableSort | null>(null);
	// Deep-link seed only — deliberately the INITIAL value (the switch owns the state after mount).
	let view = $state<string>(untrack(() => initialSegment));
	let restoring = $state<string | null>(null);

	const createAction = $derived(actions?.create);
	const canCreateClient = $derived(Boolean(createAction));
	let createOpen = $state(untrack(() => initialCreate));
	let creating = $state(false);
	let createError = $state<string | null>(null);
	let createField = $state<'email' | null>(null);
	let existingUid = $state<string | null>(null);
	let clientType = $state<'individual' | 'organisation'>('individual');
	let firstName = $state('');
	let surname = $state('');
	let orgName = $state('');
	let subType = $state('');
	let email = $state('');
	let mobilePhone = $state('');
	const isOrg = $derived(clientType === 'organisation');
	// An email or mobile the field has already flagged cannot be sent: the issue is on screen under
	// the box, and Create staying live would only fetch the same refusal back from the action.
	const canSubmitCreate = $derived(
		(isOrg ? orgName.trim().length > 0 : surname.trim().length > 0) &&
			!emailIssue(email) &&
			!phoneIssue(mobilePhone)
	);

	function resetCreate() {
		clientType = 'individual';
		firstName = '';
		surname = '';
		orgName = '';
		subType = '';
		email = '';
		mobilePhone = '';
		createError = null;
		createField = null;
		existingUid = null;
		creating = false;
	}

	function goToCreated(uid: string) {
		if (actions?.oncreated) {
			actions.oncreated(uid);
			return;
		}
		window.location.assign(links.client(uid));
	}

	// Closed is a separate population, not a filter of the live book (the API returns one or the
	// other), so the Closed status switches the source list rather than narrowing it.
	const archivedView = $derived(view === 'archived');
	const watched = watchHoldings(() => holdings);
	const liveRows = $derived(applyHoldings(clients, watched.current));
	const closedRows = $derived(applyHoldings(archived, watched.current));
	const source = $derived<ClientListRow[]>(archivedView ? closedRows : liveRows);

	// Server-driven when `listing` + `onquery` are supplied: `clients` is one page, the API
	// applied the search and filters, and this component only asks for changes.
	const server = $derived(!!listing && !!onquery && !archivedView);

	/** The table's sort. In SERVER mode it mirrors the query — the API did the ordering, so the rows
	 *  arrive ready; locally it is `localSort` over the whole population, falling back to the
	 *  table's own default column when no header has been clicked. */
	const localOrder = $derived.by(() => localSort ?? defaultSort(columns));

	const sort = $derived<DataTableSort | null>(
		server && listing ? (listing.sort ? { key: listing.sort, dir: listing.dir } : null) : localOrder
	);

	/** Hand a column click to the SERVER, so the order is over the whole book and not the page.
	 *
	 *  Sorting a server-paged listing locally is a lie: it reorders the 25 rows in hand and calls
	 *  the result "by value", while the largest client in the book may be three pages away and never
	 *  moves. Back to page one too, for the same reason a filter change is — page 4 of a re-ordered
	 *  list is a set of rows nobody asked for. */
	function chooseSort(next: DataTableSort | null): void {
		if (!server || !listing || !onquery) {
			localSort = next;
			return;
		}
		onquery({ ...listing, sort: next?.key ?? null, dir: next?.dir ?? 'asc', offset: 0 });
	}
	const term = $derived(listing && !archivedView ? listing.q : localQuery);

	/** The filter bar's several-values-per-key shape, for the server mode. Closed is stripped: it
	 *  is answered by `view`, not by the query — see `setBar`. */
	const barValue = $derived.by(() => {
		const out: Record<string, string[]> = {};
		if (!server) {
			const local: Record<string, string[]> = {};
			if (archivedView) local.status = ['closed'];
			else if (statusFilter.length) local.status = statusFilter;
			if (adviserFilter.length) local.adviser = adviserFilter;
			if (custodianFilter.length) local.custodian = custodianFilter;
			if (typeFilter.length) local.type = typeFilter;
			if (accountTypeFilter.length) local.account_type = accountTypeFilter;
			if (valueFilter.length) local.value = valueFilter;
			return local;
		}
		for (const key of BAR_KEYS) {
			const raw = manyFilters(listing?.filters[key]);
			if (RANGE_KEYS.includes(key)) {
				const pair = rangePair(raw);
				if (pair) out[key] = pair;
				continue;
			}
			const values = raw.filter((v) => v !== ALL);
			if (values.length) out[key] = values;
		}
		return out;
	});

	const BAR_KEYS = ['status', 'adviser', 'custodian', 'type', 'account_type', 'value'] as const;
	const RANGE_KEYS: readonly string[] = ['value'];

	/** Kept positional: dropping a blank end would turn `['', '3000']` ("up to 3,000") into `['3000']` ("from 3,000"). */
	function rangePair(raw: readonly string[]): string[] | null {
		const pair = [raw[0] ?? '', raw[1] ?? ''].map((v) => (v === ALL ? '' : String(v).trim()));
		return pair[0] || pair[1] ? pair : null;
	}

	/** ONE navigation for the whole bar, because the bar can change several filters at once (Reset)
	 *  and two `setFilter` calls would page the listing twice. */
	function setBar(next: Record<string, string[]>) {
		const wantsClosed = (next.status ?? []).includes('closed');
		// CLOSED IS EXCLUSIVE. It is a different population, not a status the live query can answer,
		// so it cannot be held alongside Active — ticking it drops the rest, and ticking anything
		// else drops it. The alternative is a status nobody can ever combine and a table that
		// silently returns nothing.
		if (wantsClosed && !archivedView) {
			view = 'archived';
			onsegment?.('archived');
			return;
		}
		if (!wantsClosed && archivedView) {
			view = 'live';
			onsegment?.('live');
		}
		if (!server) {
			statusFilter = (next.status ?? []).filter((v) => v !== 'closed');
			valueFilter = rangePair(next.value ?? []) ?? [];
			adviserFilter = next.adviser ?? [];
			custodianFilter = next.custodian ?? [];
			typeFilter = next.type ?? [];
			accountTypeFilter = next.account_type ?? [];
			return;
		}
		if (!listing || !onquery) return;
		const filters = { ...listing.filters };
		for (const key of BAR_KEYS) {
			if (RANGE_KEYS.includes(key)) {
				const pair = rangePair(next[key] ?? []);
				if (pair) filters[key] = pair;
				else delete filters[key];
				continue;
			}
			const values = (next[key] ?? []).filter((v) => v && v !== ALL && v !== 'closed');
			if (values.length) filters[key] = values;
			else delete filters[key];
		}
		onquery({ ...listing, filters, offset: 0 });
	}

	// CLOSED is a status here, which is what let the Live book / Archived switch go: a segmented
	// control and a status filter were two controls narrowing by the same idea, and the switch was
	// the one you had to notice to understand why a client was missing.
	// A fixed vocabulary — closed is a status, not a separate book — with the API's counts beside
	// each value, the same totals the facet-built filters show.
	const statusOptions = $derived(
		[
			{ value: 'active', label: 'Active' },
			{ value: 'lead', label: 'Lead' },
			{ value: 'suspended', label: 'Suspended' },
			{ value: 'closed', label: 'Closed' }
		].map((option) => ({ ...option, count: facetCounts?.status?.[option.value] }))
	);

	// The API's facets when server-driven; from the rows otherwise. The archived segment is a
	// different population the API returns whole, so it keeps the local options.
	const adviserOptions = $derived(
		server
			? optionsFromFacet(facets, 'adviser', undefined, facetCounts)
			: optionsFrom(source, (c) => c.advisers)
	);
	const custodianOptions = $derived(
		server
			? optionsFromFacet(facets, 'custodian', custodianLabel, facetCounts)
			: optionsFrom(source, (c) => c.custodians ?? [], custodianLabel)
	);
	/** Labelled by `clientTypeLabel`; `humanise` covers a value the vocabulary does not know rather
	 *  than dropping it. */
	const typeLabel = (value: string) => clientTypeLabel(value) ?? humanise(value);
	const typeOptions = $derived(
		server
			? optionsFromFacet(facets, 'type', typeLabel, facetCounts)
			: optionsFrom(source, (c) => [c.type, c.client_type ?? c.type], typeLabel)
	);
	/** The wrappers present in the book. Server-driven it is the API's facet, computed over the
	 *  whole match set — derived from the rows on screen it could only offer what this page happens
	 *  to hold, so paging would change what you may filter by. */
	const accountTypeOptions = $derived(
		server
			? optionsFromFacet(facets, 'account_type', humanise, facetCounts)
			: optionsFrom(source, (c) => c.account_types ?? [], humanise)
	);

	/** What the bar may offer. `options` is each filter's whole vocabulary, so a value paged in from
	 *  behind the window is still pickable. */
	const filterDefs = $derived<FilterDef[]>([
		{ key: 'status', label: 'Status', options: statusOptions },
		{
			key: 'adviser',
			label: 'Adviser',
			options: adviserOptions,
			searchPlaceholder: 'Search advisers…'
		},
		{ key: 'custodian', label: 'Custodian', options: custodianOptions },
		{ key: 'type', label: 'Client type', options: typeOptions },
		{ key: 'account_type', label: 'Account type', options: accountTypeOptions },
		{
			key: 'value',
			label: 'Portfolio value',
			options: [],
			kind: 'numberrange',
			currency: 'GBP',
			...(rangeFromBounds(bounds, 'value') ?? {})
		}
	]);

	const dirty = $derived(
		server
			? isFiltered(listing!)
			: statusFilter.length > 0 ||
					adviserFilter.length > 0 ||
					custodianFilter.length > 0 ||
					typeFilter.length > 0 ||
					accountTypeFilter.length > 0 ||
					valueFilter.length > 0
	);
	// AND THE SEARCH TERM, which `dirty` deliberately does not carry (the toolbar reads it
	// separately). `accounts-listing` and `transactions-listing` both got this and this one did
	// not, so the CLOSED segment — the only local-mode state on this listing — answered a search
	// matching nothing with "Nothing closed" and no way out, which is the exact shape those two
	// were changed to avoid. In server mode `isFiltered` already counts `q`, so this adds nothing
	// there; it is the local half that was missing.
	const narrowed = $derived(dirty || term.trim() !== '');
	function clear() {
		if (server && listing && onquery) {
			onquery({ ...listing, q: '', filters: {}, offset: 0 });
			return;
		}
		statusFilter = [];
		adviserFilter = [];
		custodianFilter = [];
		typeFilter = [];
		accountTypeFilter = [];
		valueFilter = [];
		// AND THE SEARCH BOX. In local mode the term lives here rather than in the query, so a Clear
		// that reset only the selects left the text in place and the list still empty — an action
		// offered as the way out that does not take it. Harmless while nothing surfaced Clear inside
		// the empty state; a dead end the moment one did.
		localQuery = '';
	}
	// The status filter belongs to the live book: every closed row reads as Closed, so a status
	// carried into that segment would silently empty a list the adviser can't see is filtered.
	$effect(() => {
		if (archivedView) statusFilter = [];
	});

	const rows = $derived(
		server
			? source
			: source.filter(
					(c) =>
						(archivedView || matches(statusFilter, clientStatus(c))) &&
						matchesAny(adviserFilter, c.advisers) &&
						matchesAny(custodianFilter, c.custodians ?? []) &&
						matchesAny(typeFilter, [c.type, c.client_type ?? c.type]) &&
						matchesAny(accountTypeFilter, c.account_types ?? []) &&
						withinRange(c.portfolio_value ?? 0, valueFilter) &&
						search(localQuery, clientName(c), c.uid, c.email)
				)
	);
	// Closed clients and read-only mounts are complete arrays rather than API pages. They still
	// obey the same 25-row contract; the table owns Y scrolling and this pager remains reachable.
	let localPage = $state(1);
	const localPageCount = $derived(Math.max(1, Math.ceil(rows.length / PAGE_SIZE)));
	$effect(() => {
		void localQuery;
		void statusFilter;
		void adviserFilter;
		void custodianFilter;
		void typeFilter;
		void accountTypeFilter;
		void valueFilter;
		void archivedView;
		localPage = 1;
	});
	$effect(() => {
		if (localPage > localPageCount) localPage = localPageCount;
	});

	// A failed read must never read as "no clients": say which it is, in the table as well as in the
	// banner above it, because the table is where an adviser looks for their book.
	//
	// SPLIT INTO TITLE AND BODY, and the failure cases carry a TONE. The sentence was right and the
	// treatment was not: one line of muted text in a table cell reads the same whether the book is
	// empty or the read fell over, and a reader matches the shape before the words. `tone="warning"`
	// puts the two failures in `LoadErrorBanner`'s colours — the same ones the banner above this
	// table already uses to say the same thing.
	const failed = $derived(archivedView ? archivedError : loadError);
	/**
	 * THE FILTERS, NOT THE ROW COUNT. `source` is ONE PAGE in server mode, so a filter that matches
	 * nothing empties it and `source.length === 0` then claims the book is empty — the same wrong
	 * discriminator this branch fixed in `accounts-listing`, where it told a firm holding 379
	 * accounts it had none.
	 *
	 * It had been survivable while the empty state was one sentence. It stopped being survivable
	 * when the Clear action was added beside it: the box then said "No clients yet", "Add your first
	 * client" and "Clear filters" at the same time — three statements, two of them false.
	 */
	const emptyTitle = $derived(
		archivedView
			? archivedError
				? 'Closed clients couldn’t be loaded'
				: narrowed
					? 'No closed clients match your filters'
					: 'Nothing closed'
			: loadError
				? 'Your clients couldn’t be loaded'
				: narrowed
					? 'No clients match your filters'
					: 'No clients yet'
	);
	// Two corrections to this block. The failure branches said "Retry above" unconditionally while
	// the banner's button is `{#if onRetry}` and `onretry` is an optional prop — true for all three
	// mounts today, and a promise the next mount can silently break, so the sentence no longer
	// names a control it cannot see. And the filter miss had NO body at all: every other narrowed
	// state in the estate now says what clearing gets back, and a title on its own is half a state.
	const emptyDescription = $derived(
		archivedView
			? archivedError
				? 'Closed clients could not be loaded.'
				: narrowed
					? 'Try a different search or filter, or clear them to see everything closed.'
					: 'Closed clients keep their history and can be reopened from here.'
			: loadError
				? 'Clients could not be loaded.'
				: narrowed
					? 'Try a different search or filter, or clear them to see your whole book.'
					: canCreateClient
						? 'Add your first client to start building your book.'
						: 'Clients appear here once they’re added to your book.'
	);

	// Seven columns. TYPE is gone: individual-or-organisation is carried by the avatar (a person or a
	// building), which states it in the space the row already spends on identity rather than a column
	// of its own — and the Type filter above is untouched, so it is still how you narrow by it.
	//
	// GROWTH is back, and only because it is coloured now. It was cut earlier for being a figure you
	// could not compare down the column; green-up/red-down makes the DIRECTION scannable, which is a
	// different job from comparing magnitudes and one a column can actually do.
	//
	// Ceilings from text-shell.ts on the free-text columns. `adviser` had the worst version of the
	// clipping bug: `advisers.join(', ')` has no upper bound, and its `truncate` sat on an inline span
	// where `overflow` does not apply, so a client with three advisers stretched the whole table.
	// EVERY COLUMN DECLARES ITS SHARE, and the table is `fixed`. Capping the cells was not enough and
	// could not have been: under the default `auto` layout the browser hands the leftover width out in
	// proportion to CONTENT, so Client and Adviser — the two columns holding sentences — took most of
	// it while Accounts stayed at the width of a single digit. A ceiling stops a column growing past a
	// limit; it does not stop it claiming slack below one. Percentages rather than rem so the row
	// keeps its proportions at any container width, and they total 100 at full width (a `hideBelow`
	// column drops out and the browser rescales the rest, which is the behaviour we want).
	//
	// The columns of a KNOWN size get it in rem rather than a share. Currency is one flag pill under
	// a head that reads "Currency" with its sort arrow (5.25rem); Status is one badge whose widest
	// word is "Suspended" (6.5rem); the three money columns are a figure of at most eight digits —
	// "£12,345,678.90" is ~120px, a signed growth a little more — so Value and Growth take 8.5rem
	// and Cash 8rem. A share gave each too much on a wide screen and too little on a narrow one,
	// where the badge clipped and a seven-figure value spilled into the Currency
	// pill beside it. Accounts (three tags and a count) and Adviser (a face, "Diane J.", a count)
	// are bounded too and step up with the table. Client is the ONLY column with no width: under
	// `table-fixed` the one auto column takes whatever the others leave, which is exactly what the
	// column holding the sentences — the name, and the date of birth or the town under it — should
	// do.
	//
	// Columns drop by the TABLE's width (`hideBelowWidth`), not the viewport: this listing is also
	// mounted in admin's operate-as-firm pane and beside rails, where the viewport says nothing
	// about the room the table has, and a money column that stays at a width that cannot hold its
	// figure spills rather than hides. The same rungs as the dashboard's `BOOK_COLUMNS` — Adviser
	// below 2xl, Cash below 4xl, Growth below 5xl — because this IS that book.
	const baseColumns: DataTableColumn<ClientListRow>[] = [
		{
			key: 'name',
			header: 'Client',
			sortable: true,
			sortDefault: true,
			cellClass: 'truncate',
			sortValue: (c) => clientName(c)
		},
		{
			key: 'accounts',
			header: 'Accounts',
			sortable: true,
			// TAGS now, not a count: "GIA · ISA · SIPP" answers what a book is actually scanned for
			// ("which of my clients has a SIPP?"), where "3" cannot. LEFT-aligned, because a row of
			// badges is a list and reads from the left like every other list in the product — the
			// centring below was right for a single digit and wrong for this.
			//
			// SORTED BY THE COUNT still (`sortValue`), which is the only total order a set of tags
			// has and the question this column's ordering already answered. Ordering by whichever
			// wrapper sorts first would order the book by an accident of the alphabet.
			defaultDir: 'desc',
			// DO NOT ADD PADDING HERE for spacing. Under `table-fixed` a column still cannot render
			// narrower than its own unbreakable content, and every head is `whitespace-nowrap`:
			// "ACCOUNTS" is ~62px, so a `pr-10` here would put this column's minimum near 110px. A
			// wide viewport clears that and a ~900px content pane does not, at which point the
			// declared shares stop being honourable and the whole table overflows sideways.
			//
			// In rem, stepped by the TABLE's width: three tags and a "+N" are ~150px on one line, and
			// a share of a narrow table (89px of 635) put the third tag under the Currency pill. Up to
			// `4xl` the column takes 7rem and `AccountTags` wraps to a second line inside the row the
			// subline already made two lines tall; from `4xl`, where Cash joins, 10rem holds them on
			// one.
			headerClass: 'w-[7rem] @4xl:w-[10rem]',
			sortValue: (c) => c.account_count
		},
		{
			key: 'adviser',
			header: 'Adviser',
			sortable: true,
			hideBelowWidth: '2xl',
			// NO MANUFACTURED GUTTER: a 64px `pl` once separated this from a right-aligned count in
			// Accounts; the tags read from the left now and the boundary does the job on its own.
			//
			// In rem, stepped like Accounts: `AdviserStack` is a badge-height face, "Diane J." and a
			// count — bounded — and a 12% share of the table this column has just reappeared in (86px
			// of 716) clipped even that short form. 7.5rem holds one face and a name; 10rem from `4xl`
			// holds three faces and "Diane J. +2".
			headerClass: 'w-[7.5rem] @4xl:w-[10rem]',
			cellClass: 'truncate',
			sortValue: (c) => c.advisers.join(', ')
		},
		{
			// The client's base currency (ISO). The symbol is on the figures; this column
			// says which currency those figures are in. One `CurrencyBadge`, a known size.
			key: 'currency',
			header: 'Currency',
			sortable: true,
			headerClass: 'w-[5.25rem]',
			sortValue: (c) => c.currency
		},
		{
			key: 'value',
			header: 'Value',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			headerClass: 'w-[8.5rem]',
			sortValue: (c) => c.portfolio_value
		},
		{
			key: 'cash',
			header: 'Cash',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelowWidth: '4xl',
			headerClass: 'w-[8rem]'
		},
		{
			key: 'growth',
			header: 'Growth',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelowWidth: '5xl',
			headerClass: 'w-[8.5rem]'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[6.5rem]',
			sortValue: (c) => clientStatus(c)
		}
	];
	// Reopening is offered on the LIST as well as the record: Closed stays loadable, but this
	// segment is still the place you find everyone who has been closed. A width, because a second
	// auto column would split Client's remainder with it half-and-half.
	const columns = $derived<DataTableColumn<ClientListRow>[]>(
		archivedView && actions?.restore
			? [...baseColumns, { key: 'restore', header: '', align: 'right', headerClass: 'w-[6rem]' }]
			: baseColumns
	);

	// Server mode: the API ordered and cut, so the rows arrive ready. Local mode: sort the WHOLE
	// population and only then slice — `DataTable` no longer sorts for us (passing `onSort` puts it
	// in server-sorted mode), and it could only ever have sorted the page it was handed anyway, so
	// "by value" reordered 25 rows while the largest client sat on another page.
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

{#snippet addFirstClient()}
	<!-- The description said "Add your first client" while the control that does it — the same
	     dialog the header's New client button opens — sat unoffered twenty lines away. Telling
	     someone to do a thing is not offering it. -->
	<Button size="sm" onclick={() => (createOpen = true)}>New client</Button>
{/snippet}

{#snippet nothingToShow()}
	<!-- No icon on a failure: the tone already says what it is, and a friendly glyph over
	     "couldn't be loaded" reads as decoration on bad news.
	     TOP LEVEL, not beside the table: a snippet declared inside an `{#if}` is scoped to it, and
	     the table that uses this one is outside that block.
	     THE FAILURE IS TESTED FIRST in the action too. `dirty` came first there, so a read that fell
	     over while a filter happened to be set offered "Clear filters" under "Your clients couldn't
	     be loaded" — inviting the reader to fix a narrowing that is not why the list is empty. The
	     title and description already put the failure ahead of `dirty`; only the action disagreed. -->
	<EmptyState
		compact
		icon={failed ? undefined : UsersIcon}
		tone={failed ? 'warning' : 'neutral'}
		title={emptyTitle}
		description={emptyDescription}
		action={failed
			? undefined
			: narrowed
				? clearFilters
				: canCreateClient && !archivedView && source.length === 0
					? addFirstClient
					: undefined}
	/>
{/snippet}

<!-- ONE SCREEN from `md`. The page is a flex column the height of the viewport under the top bar,
     and the LIST is the only thing that scrolls — so the toolbar and the pager stay put however
     long the book is. On a phone the PAGE scrolls instead: a nested table scroller inside a
     viewport-tall pane trapped the rows, and `table-fixed` columns overlapped their nowrap cells.
     `-m-4 md:-m-6` cancels the shell's own canvas padding and re-applies it inside; `min-h-0` on
     the card (from `md`) is what lets a flex child shrink below its content. The topbar crumb
     already names the page, so there is no heading. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#snippet listingActions()}
		{#if canCreateClient}
			<Button size="sm" onclick={() => (createOpen = true)}>
				<PlusIcon class="size-4" aria-hidden="true" /> New client
			</Button>
		{/if}
	{/snippet}

	<!-- A failed read must never render as an empty book. Retry re-runs the app's load through a
	     fresh request, where its hooks refresh an expired token. -->
	{#if loadError}
		<LoadErrorBanner message="Couldn’t load your clients. Try again." onRetry={onretry} />
	{/if}
	{#if archivedError && archivedView}
		<LoadErrorBanner message="Couldn’t load closed clients. Try again." onRetry={onretry} />
	{/if}

	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			<ListingToolbar
				class="shrink-0"
				query={term}
				placeholder="Search clients, reference or email"
				{dirty}
				onclear={clear}
				onsearch={server && listing && onquery
					? (next) => onquery({ ...listing, q: next, offset: 0 })
					: (next) => (localQuery = next)}
				actions={canCreateClient ? listingActions : undefined}
				{filterDefs}
				filterValue={barValue}
				onfilters={setBar}
			/>

			<DataTable
				{columns}
				rows={displayedRows}
				{sort}
				onSort={chooseSort}
				scroll
				fixed
				getKey={(c) => c.id}
				empty={nothingToShow}
			>
				{#snippet cell(c, column)}
					{#if column.key === 'name'}
						<!-- The avatar states what the Type column used to: a person, a child or a building.
						     `ClientAvatar` is one muted disc where only the glyph varies, so the column reads as
						     one shape and the ICON is the only difference. The name is right beside it, so a set
						     of initials would add nothing the row does not already say.
						     Closed clients stay loadable, so the name is still a link. -->
						{@const subline = clientSubline(c)}
						<span class="flex min-w-0 items-center gap-2.5">
							<ClientAvatar name={clientName(c)} kind={clientKind(c)} size="sm" />
							<!-- The name, and under it the date of birth — or, for a client with none (an
							     organisation), where they are (town, postcode, country: the record header's
							     address line without its street). The reference used to sit here, on the
							     argument that two clients can share a name; true, but nobody scans a listing
							     for ids, and it lives on the record header now. The date is what tells two
							     Alan Hornes apart and what a custodian asks for first, so it earns the line.
							     Both lines truncate in their own width: `min-w-0` so the text is what gives
							     way inside the spine column, not the avatar beside it. -->
							<span class="block min-w-0">
								<TruncatedText
									text={clientName(c)}
									href={links.client(c.uid)}
									class="font-medium"
								/>
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
						<!-- Growth under the value while its own column is hidden: the same figure, on
						     its own line, and gone again the moment the column comes back. The class is
						     the inverse of that column's hide breakpoint, so the two cannot both show. -->
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
						<!-- The one column where direction is the fact being scanned for, so it is tinted by
						     sign. The +/- still renders, so the colour is never carrying it alone. -->
						<Money amount={c.growth} currency={c.currency || 'GBP'} signed tone="directional" />
					{:else if column.key === 'status'}
						{#if archivedView}
							<StatusBadge tone={statusTone('closed')}>Closed</StatusBadge>
						{:else}
							{@const s = clientStatus(c)}
							<StatusBadge tone={statusTone(s)}>{humanise(s)}</StatusBadge>
						{/if}
					{:else if column.key === 'restore' && actions?.restore}
						<form
							method="POST"
							action={actions.restore}
							use:enhanceForm={() => {
								restoring = c.uid;
								return async ({ result, update }) => {
									restoring = null;
									if (result.type === 'success') toast.success('Client reopened');
									else if (result.type === 'failure')
										toast.error(String(result.data?.error ?? 'Couldn’t reopen this client.'));
									await update();
								};
							}}
						>
							<input type="hidden" name="uid" value={c.uid} />
							<Button type="submit" variant="outline" size="sm" disabled={restoring === c.uid}>
								{restoring === c.uid ? 'Reopening…' : 'Reopen'}
							</Button>
						</form>
					{/if}
				{/snippet}
			</DataTable>

			{#if server && listing && onquery && matchCount !== null}
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

			<!-- The provenance footnote is gone. It said three things and two of them had stopped being
			     true: there IS pagination now (the pager below states the range), and "every visible
			     client is listed" contradicts it. The third — that Value is a batch snapshot, with its
			     as-at date — is real, and it is stated where the figure is: the account record carries
			     the stamp, and `docs/design-language.md` asks for provenance as a compact stamp rather
			     than a paragraph under a table.
			     The archived note stays: it answers "what happens if I restore this", which nothing else
			     on the screen does. -->
			{#if archivedView}
				<p class="text-muted-foreground shrink-0 text-xs">
					Closed clients keep all their history; reopening one puts them straight back on the book.
				</p>
			{/if}
		</CardContent>
	</Card>
</div>

{#if createAction}
	<!-- FORM DRAWER (`sm:max-w-xl`): a name, an email and a mobile do not need the illustration
	     floor. The button above is the gate — this sheet never mounts without the create action,
	     so a `?new=1` URL cannot open a write the caller is not allowed. -->
	<Sheet.Root
		bind:open={createOpen}
		onOpenChange={(next) => {
			if (!next) resetCreate();
		}}
	>
		<Sheet.Content side="right" class="gap-0 p-0 sm:max-w-xl">
			<form
				method="POST"
				action={createAction}
				class="flex h-full min-h-0 flex-col"
				use:enhanceForm={() => {
					creating = true;
					createError = null;
					createField = null;
					existingUid = null;
					return async ({ result }) => {
						creating = false;
						if (result.type === 'success') {
							const uid = String(result.data?.uid ?? '');
							toast.success('Client created');
							createOpen = false;
							resetCreate();
							if (uid) goToCreated(uid);
							else onretry?.();
							return;
						}
						if (result.type === 'failure') {
							createError = String(result.data?.error ?? 'Couldn’t create this client.');
							createField = result.data?.field === 'email' ? 'email' : null;
							existingUid =
								typeof result.data?.existingUid === 'string' ? result.data.existingUid : null;
						}
					};
				}}
			>
				<Sheet.Header class="border-b px-4 pt-4 pb-3">
					<Sheet.Title>New client</Sheet.Title>
					<Sheet.Description>
						Add someone to your book. Their details can be completed on the record.
					</Sheet.Description>
				</Sheet.Header>

				<div class="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-4">
					{#if createError}
						<Alert variant="destructive">
							<AlertDescription>{createError}</AlertDescription>
							{#if existingUid}
								<a
									href={links.client(existingUid)}
									class="text-destructive-strong mt-1 inline-block text-sm font-medium underline"
								>
									Open existing client
								</a>
							{/if}
						</Alert>
					{/if}

					<div class="space-y-1.5">
						<span class="block text-sm font-medium">Client type</span>
						<Segmented
							options={[
								{ value: 'individual', label: 'Individual' },
								{ value: 'organisation', label: 'Organisation' }
							]}
							bind:value={clientType}
						/>
						<input type="hidden" name="type" value={clientType} />
					</div>

					{#key clientType}
						{#if isOrg}
							<div class="space-y-1.5">
								<Label for="new-client-name">Organisation name</Label>
								<Input
									id="new-client-name"
									name="name"
									bind:value={orgName}
									placeholder="Oakden Trust"
									autofocus
								/>
							</div>
							<div class="space-y-1.5">
								<span class="block text-sm font-medium">Kind</span>
								<Segmented options={ORGANISATION_SUB_TYPES} bind:value={subType} />
								<input type="hidden" name="sub_type" value={subType} />
								<p class="text-muted-foreground text-xs">
									Leave unset if it is not yet established.
								</p>
							</div>
						{:else}
							<div class="grid gap-4 sm:grid-cols-2">
								<div class="space-y-1.5">
									<Label for="new-client-first">First name</Label>
									<Input
										id="new-client-first"
										name="first_name"
										bind:value={firstName}
										placeholder="Theo"
										autofocus
									/>
								</div>
								<div class="space-y-1.5">
									<Label for="new-client-surname">Surname</Label>
									<Input
										id="new-client-surname"
										name="surname"
										bind:value={surname}
										placeholder="Caldwell"
									/>
								</div>
							</div>
						{/if}
					{/key}

					<div class="space-y-1.5">
						<Label for="new-client-email">Email</Label>
						<EmailInput
							id="new-client-email"
							name="email"
							value={email}
							onchange={(v) => (email = v)}
							placeholder="theo@caldwell.test"
							invalid={createField === 'email'}
						/>
					</div>
					<div class="space-y-1.5">
						<!-- THE number staff ring, and the only one creation asks for. A contact's other
						     numbers are a repeatable list, which is a detail-page job: a New client drawer
						     that opens with a "+ Add number" is asking for work nobody has on the call.
						     The shared phone field, as on the record: it refuses letters as they are typed
						     and says what is wrong under the box, rather than waiting for the API to
						     refuse the number on Create. -->
						<Label for="new-client-mobile">Mobile number</Label>
						<PhoneNumberInput
							id="new-client-mobile"
							name="mobile"
							value={mobilePhone}
							onchange={(v) => (mobilePhone = v)}
						/>
					</div>
				</div>

				<Sheet.Footer class="flex-row justify-between border-t">
					<Button type="button" variant="ghost" onclick={() => (createOpen = false)}>Cancel</Button>
					<Button type="submit" disabled={!canSubmitCreate || creating}>
						{creating ? 'Creating…' : 'Create client'}
					</Button>
				</Sheet.Footer>
			</form>
		</Sheet.Content>
	</Sheet.Root>
{/if}
