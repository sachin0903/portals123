/**
 * The clients-book table's pure logic — row shape, derived status, column definitions and the
 * filter predicate.
 *
 * It lives outside the component so the dashboard's book and the `/clients` listing can render the
 * SAME table: the dashboard used to show a 6-row "top clients" teaser built from a different
 * endpoint with different columns, so an adviser comparing the two screens saw two answers to
 * "what does my book look like?". Column defs, the derived status and the search fields are
 * declared once, here, and asserted directly in `routes/dashboard.test.ts`.
 *
 * Filtering stays client-side over the already-fetched array, exactly as `/clients` does it:
 * `GET /crm/contacts` pages when `limit` is set (see app/listing.py). Cash and growth are the
 * stored valuation, on the same row as the portfolio value.
 */
import { clientName } from '../clients/format.js';
import { clientStatus, type ClientStatus } from '../clients/status.js';
import type { DataTableColumn } from '../../ui/data-table/index.js';
import { matches, matchesAny, search, withinRange } from '../listing/index.js';

/** One row of `GET /crm/contacts` as the book table reads it. Structural on purpose: the loader's
 *  richer row type (the API also returns `phone`, `address`, `value_as_of`…) is assignable to it. */
export type BookRow = {
	id: number;
	uid: string;
	type: 'individual' | 'organisation';
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
	email: string | null;
	/** ISO date. The line under the name (`clientSubline`); the API always sends it. */
	dob?: string | null;
	/** `crm.contacts.address` as stored. The line under a name with no date of birth — an
	 *  organisation's — prints the town, postcode and country from it (`clientSubline`). */
	address?: unknown;
	advisers: string[];
	enabled: boolean;
	deleted_at?: string | null;
	provider_status?: string | null;
	service_type: 'advised' | 'execution_only' | null;
	account_count: number;
	/** The distinct wrappers held (`GIA`, `ISA`, `SIPP`…), sorted by the API — the Accounts cell's
	 *  tags, as on /clients. Optional only because older fixtures predate it; the API always
	 *  sends it. */
	account_types?: string[];
	/** Accounts that can hold value (not draft, not closed) — the basis of every money figure. */
	valued_account_count: number;
	portfolio_value: number;
	/** The client's base currency. Absent means the figures are shown as GBP. */
	currency?: string | null;
	/** Null when no valued account has a stated figure. Zero is a measured flat position. */
	cash: number | null;
	growth: number | null;
};

/** Derived, never stored: a client is a Lead until they hold an account, Suspended when
 *  the custodian says so, Closed once the firm closes them. One function so the badge, the
 *  filter and the sort can't disagree — the listing's `clientStatus`, not a second copy. */
export type { ClientStatus };

export function statusOf(c: {
	account_count: number;
	deleted_at?: string | null;
	provider_status?: string | null;
}): ClientStatus {
	return clientStatus(c);
}

export const STATUS_VARIANT = {
	active: 'secondary',
	lead: 'outline',
	suspended: 'outline',
	closed: 'outline'
} as const satisfies Record<ClientStatus, 'secondary' | 'outline' | 'destructive'>;

/**
 * The statuses a LIVE book can actually be narrowed to.
 *
 * `closed` is deliberately absent. The dashboard reads `/crm/contacts` without `archived=true`, so
 * closed clients are not in the population at all — offering the option meant a filter that could
 * only ever empty the table, which reads as "this firm has no clients" rather than as a filter
 * that does not apply here. Closed clients are reached from the clients listing, where choosing
 * Closed switches population instead of narrowing this one.
 *
 * This is also why the all-option is labelled "All active statuses" wherever it is shown: "All
 * statuses" claims the closed ones are included.
 */
export const STATUS_OPTIONS: { value: ClientStatus; label: string }[] = [
	{ value: 'active', label: 'Active' },
	{ value: 'lead', label: 'Lead' },
	{ value: 'suspended', label: 'Suspended' }
];

/** The toolbar's state. `ALL` (from `@graphene/ui/book/listing`) is the unset sentinel. */
export type BookFilters = {
	query: string;
	/** Each filter carries the value(s) the bar holds. A STRING is one value (what a single-select
	 *  stored), an ARRAY is a multi-select's ticks, and an empty array is no filter — `matches`
	 *  answers all three, so the predicates below did not change. */
	status: string | string[];
	adviser: string | string[];
	type: string | string[];
	value?: string[];
};

/**
 * Search + status/adviser/type filters in one pass. Does not mutate the input; sorting is the
 * DataTable's job.
 *
 * `archived` suppresses the status predicate: every closed row reads as Closed, so a status
 * carried into that segment would silently empty a list the adviser can't see is filtered.
 */
export function filterBook<T extends BookRow>(
	rows: T[],
	f: BookFilters,
	opts: { archived?: boolean } = {}
): T[] {
	return rows.filter(
		(c) =>
			(opts.archived === true || matches(f.status, statusOf(c))) &&
			matchesAny(f.adviser, c.advisers) &&
			matches(f.type, c.type) &&
			// `valued_account_count` first: a client with nothing valued is UNKNOWN, not nought, so
			// it must not read as "under £10,000". See `crm.py::_CONTACT_FILTERS['value']`.
			withinRange(c.valued_account_count > 0 ? c.portfolio_value : Number.NaN, f.value) &&
			search(f.query, clientName(c), c.uid, c.email, ...c.advisers)
	);
}

/** The book's columns, in reading order. `Money` cells render right-aligned and tabular.
 *
 *  Each column declares its SHARE and the table is `fixed` (see DataTable's `fixed`): under the
 *  default `auto` layout the browser hands leftover width out in proportion to CONTENT, so Client and
 *  Adviser — the two columns holding sentences — took most of it while Accounts stayed the width of a
 *  single digit. Same widths as the shared /clients listing, because this IS that book and the two
 *  screens must not read differently. The columns of a KNOWN size get it in rem: Currency is one
 *  flag pill under a head reading "Currency" with its sort arrow, Status is one badge whose widest
 *  word is "Suspended", and the money columns hold a figure of at most eight digits ("£12,345,678.90"
 *  is ~120px; a signed growth a little more). A share gave each too much on a wide screen and too
 *  little on a narrow one — the badge clipped and a seven-figure value spilled into the pill beside
 *  it. Accounts (three tags and a count) and Adviser (a face, "Diane J.", a count) are bounded too
 *  and step up with the table. Client is the ONLY column with no width: under `table-fixed` the one
 *  auto column takes whatever the others leave, which is what the column holding the sentences (the
 *  name, and the date of birth or the town under it) should do. */
export const BOOK_COLUMNS: DataTableColumn<BookRow>[] = [
	{
		key: 'name',
		header: 'Client',
		sortable: true,
		sortDefault: true,
		cellClass: 'truncate',
		sortValue: (c) => clientName(c)
	},
	// NO TYPE COLUMN. The avatar in the Client cell already states it — a building glyph for an
	// organisation, a person's initials otherwise — so a column repeating the same fact in words
	// spent 8% of the table saying it twice. The shared /clients listing dropped it for exactly
	// this reason, and this IS that book: the two screens must not read differently.
	//
	// The FILTER stays (see the toolbar in clients-table.svelte). Narrowing to organisations is
	// still worth doing; a column is just not how you do it.
	{
		key: 'accounts',
		header: 'Accounts',
		sortable: true,
		// TAGS, not a count — the /clients listing's `AccountTags`: "GIA · ISA · SIPP" answers what a
		// book is actually scanned for ("which of my clients has a SIPP?"), where "3" cannot.
		// LEFT-aligned, because a row of badges is a list and reads from the left like every other
		// list in the product; the centring this replaces was right for a single digit and wrong
		// for this. Still SORTED BY THE COUNT (`sortValue`), the only total order a set of tags has.
		// In rem, stepped by the table's width: 7rem with the tags wrapping to a second line while
		// the table is narrow, 10rem — three tags and a "+N" on one line — from `4xl`.
		defaultDir: 'desc',
		headerClass: 'w-[7rem] @4xl:w-[10rem]',
		sortValue: (c) => c.account_count
	},
	{
		key: 'adviser',
		header: 'Adviser',
		sortable: true,
		hideBelowWidth: '2xl',
		// Stepped like Accounts: 7.5rem holds a badge-height face and "Diane J."; 10rem from `4xl`
		// holds three faces and the count. A 12% share clipped even the short name at 716px.
		headerClass: 'w-[7.5rem] @4xl:w-[10rem]',
		cellClass: 'truncate',
		sortValue: (c) => c.advisers.join(', ')
	},
	{
		key: 'currency',
		header: 'Currency',
		sortable: true,
		headerClass: 'w-[5.25rem]',
		sortValue: (c) => c.currency ?? ''
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
		headerClass: 'w-[8rem]',
		hideBelowWidth: '4xl'
	},
	{
		key: 'growth',
		header: 'Growth',
		sortable: true,
		align: 'right',
		defaultDir: 'desc',
		headerClass: 'w-[8.5rem]',
		hideBelowWidth: '5xl'
	},
	{
		key: 'status',
		header: 'Status',
		sortable: true,
		headerClass: 'w-[6.5rem]',
		sortValue: (c) => statusOf(c)
	}
];
