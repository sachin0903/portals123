/**
 * The listing query — search, filters and page — as one URL-shaped object.
 *
 * `filters.ts` still holds the client-side predicates and is still what the fixture-backed
 * listings use. This is the other half: when a listing is server-driven, the toolbar stops
 * filtering and starts *describing* what the user asked for, and the app turns that into a
 * navigation.
 *
 * **Why the URL and not component state.** A filtered listing has to survive a reload and be
 * shareable — "look at the four accounts that aren't reconciling" is a link, not a description
 * of which controls to click. It is also what makes the back button work: narrowing a list and
 * pressing back should widen it again, which local state gets wrong. The tasks inbox already
 * works this way (`book/tasks`, `onfilters`/`onshowmore`); this generalises it.
 *
 * The app owns `goto`, because `$app/navigation` is SvelteKit and this package is not.
 */

/** The page size every server-driven listing asks for.
 *
 *  25, not 50. At 50 nothing in the product paged: a firm with sixteen clients and thirty-three
 *  accounts sat inside one page on every listing, so the pager never appeared and the page size was
 *  a number nobody could observe. 25 is also what the transactions ledger already asked for
 *  explicitly, so this makes the odd one out the default. */
import { ALL } from './filters.js';

export const PAGE_SIZE = 25;

export interface ListingQuery {
	/** Free-text search. Empty means unsearched, and is omitted from the URL. */
	q: string;
	/** Filter key -> value. A key absent (or `ALL`) means that filter is off. */
	/** filter key → the chosen value(s). A STRING or an ARRAY: a single-select filter still stores
	 *  one string, and a multi-select stores the list the reader ticked. Both travel as repeated
	 *  query params, so `?status=active&status=onboarding` round-trips and a one-value filter's URL
	 *  is unchanged. */
	filters: Record<string, string | string[]>;
	/** Rows to skip. Zero is omitted from the URL so the first page has a clean link. */
	offset: number;
	limit: number;
	/** Column to order by, and which way — the SERVER's ordering, not the page's.
	 *
	 *  A listing that pages cannot sort locally and be telling the truth: sorting the 25 rows in
	 *  hand reorders the page and calls it "sorted by value", while the largest client in the book
	 *  may be three pages away. Null means the endpoint's own default order.
	 */
	sort: string | null;
	/** The order this listing falls back to with none in the URL. `apiParams` sends it;
	 *  `paramsFromQuery` keeps it out of the URL. */
	defaultSort?: { key: string; dir: 'asc' | 'desc' } | null;
	dir: 'asc' | 'desc';
	/**
	 * Every filter this listing understands — not just the ones currently set.
	 *
	 * Carried on the query so `paramsFromQuery` can DELETE a filter that has been cleared.
	 * Without it, clearing `status` left `?status=active` in the URL: the writer only ever set
	 * the keys still present, and had no way to tell a stale filter key from an unrelated param
	 * like `?focus=` that it must preserve.
	 */
	keys: readonly string[];
}

/** What the server sends back around the rows. */
export interface ListingPage<T> {
	items: T[];
	/** Matches across the whole book, not the size of `items` — see app/listing.py. */
	total: number;
	/** Per-filter option values, computed server-side over the match set. */
	facets: Record<string, string[]>;
	/** Readable names for facet values that are IDS, keyed `{facet: {value: label}}`. Optional
	 *  because most facets are already readable; a facet keyed on a uid (so that two rows with the
	 *  same display name stay distinguishable) needs them, and they must come from the server for
	 *  the same reason the options do — a facet is computed with its own filter lifted, so labelling
	 *  from the rows on screen would resolve only the value already in force. */
	facet_labels?: Record<string, Record<string, string>>;
	/** How many rows carry each facet value, with that facet's own filter lifted. */
	facet_counts?: Record<string, Record<string, number>>;
	bounds?: Record<string, number[]>;
}

/** The rows, whether the API answered with a bare list or the paged envelope. */
export function itemsOf<T>(page: T[] | ListingPage<T>): T[] {
	return Array.isArray(page) ? page : page.items;
}

export const emptyQuery = (keys: readonly string[] = [], limit = PAGE_SIZE): ListingQuery => ({
	q: '',
	filters: {},
	offset: 0,
	limit,
	sort: null,
	dir: 'asc',
	defaultSort: null,
	keys
});

/**
 * Read a query out of a URL's search params.
 *
 * `keys` names the filters this listing understands, so an unrelated param (a `?focus=` deep
 * link, a tracking tag) is not mistaken for one and sent to the API.
 *
 * A `sort` in `options` must name a column the endpoint sorts on (its `_X_SORTS` map) — it ignores
 * an unknown one, and the caret would then point at a column nothing was ordered by.
 */
export function queryFromParams(
	params: URLSearchParams,
	keys: readonly string[],
	options: number | { limit?: number; sort?: string; dir?: 'asc' | 'desc' } = PAGE_SIZE
): ListingQuery {
	const {
		limit = PAGE_SIZE,
		sort: fallbackKey,
		dir: fallbackDir = 'asc'
	} = typeof options === 'number' ? { limit: options } : options;
	const fallback = fallbackKey ? { key: fallbackKey, dir: fallbackDir } : null;
	const filters: Record<string, string | string[]> = {};
	for (const key of keys) {
		// `getAll`, so a filter the reader gave several values of survives a round trip. One
		// occurrence stays a plain string — the shape single-select listings already store.
		const raw = params.getAll(key);
		// A PAIR WITH AN EMPTY SLOT IS A RANGE, and the slot is the meaning: `?value=&value=3000`
		// is "up to 3,000", and dropping the blank leaves `['3000']` — "from 3,000".
		if (raw.length === 2 && raw.some((one) => one === '') && raw.some(Boolean)) {
			filters[key] = raw;
			continue;
		}
		const values = raw.filter(Boolean);
		if (values.length > 1) filters[key] = values;
		else if (values.length === 1) filters[key] = values[0] as string;
	}
	const offset = Number(params.get('offset') ?? 0);
	return {
		q: params.get('q') ?? '',
		filters,
		// A hand-edited or stale `offset` should not break the page — clamp, like the API does.
		offset: Number.isFinite(offset) && offset > 0 ? Math.floor(offset) : 0,
		limit,
		// The API validates `sort` against its own closed list of sortable columns and ignores
		// anything else, so a junk value orders by the endpoint's default rather than erroring.
		sort: params.get('sort') || fallback?.key || null,
		dir: params.get('sort')
			? params.get('dir') === 'desc'
				? 'desc'
				: 'asc'
			: (fallback?.dir ?? 'asc'),
		defaultSort: fallback,
		keys
	};
}

/**
 * The query as URL search params — omitting everything at its default.
 *
 * Omitting defaults is what keeps `/clients` as `/clients` rather than
 * `/clients?q=&status=&adviser=&offset=0`, and it means "is this list filtered?" can be
 * answered by looking at the address bar.
 */
/** Write one filter's values onto a params bag, KEEPING a range's empty slot: skipping it sent
 *  `['', '3000']` ("up to 3,000") as one `value=3000`, read at the far end as "from 3,000". */
function appendFilter(params: URLSearchParams, key: string, value: string | string[]): void {
	const list = Array.isArray(value) ? value : [value];
	const positional = list.length === 2 && list.some((one) => one === '') && list.some(Boolean);
	for (const one of list) if (positional || one) params.append(key, one);
}

export function paramsFromQuery(query: ListingQuery, base?: URLSearchParams): URLSearchParams {
	// Start from the current params so unrelated ones (a `?focus=` deep link) survive a filter
	// change rather than being dropped by it.
	const params = new URLSearchParams(base);
	const set = (key: string, value: string | string[]) => {
		params.delete(key);
		// `append`, not `set`, for the second value onward — a multi-select filter is repeated
		// params, which is what the routers read and what `getAll` above reads back.
		appendFilter(params, key, value);
	};
	set('q', query.q.trim());
	// Drop every filter this listing owns before writing the ones still set, so clearing one
	// removes it from the URL. Scoped to `keys` precisely so an unrelated param is untouched.
	for (const key of query.keys) params.delete(key);
	for (const [key, value] of Object.entries(query.filters)) set(key, value);
	if (query.offset > 0) params.set('offset', String(query.offset));
	else params.delete('offset');
	// The default stays out of the URL, so a `sort` there always means one the reader chose.
	const atDefault =
		!!query.defaultSort &&
		query.sort === query.defaultSort.key &&
		query.dir === query.defaultSort.dir;
	// `dir` only travels with a `sort` — on its own it names a direction for nothing.
	if (query.sort && !atDefault) {
		params.set('sort', query.sort);
		if (query.dir === 'desc') params.set('dir', 'desc');
		else params.delete('dir');
	} else {
		params.delete('sort');
		params.delete('dir');
	}
	return params;
}

/** The query as an API query string, in the shape the routers expect. */
export function apiParams(query: ListingQuery): URLSearchParams {
	const params = new URLSearchParams();
	if (query.q.trim()) params.set('q', query.q.trim());
	for (const [key, value] of Object.entries(query.filters)) appendFilter(params, key, value);
	params.set('limit', String(query.limit));
	if (query.offset > 0) params.set('offset', String(query.offset));
	if (query.sort) {
		params.set('sort', query.sort);
		params.set('dir', query.dir);
	}
	return params;
}

/** A filter read as ONE value, for a control that offers one. A multi-select's first value, so a
 *  single-select control fed a multi-valued filter shows something true rather than `[object
 *  Object]` — it cannot show the rest, which is why the two never sit on the same filter. */
export const oneFilter = (value: string | string[] | undefined): string | undefined =>
	Array.isArray(value) ? value[0] : value;

/** A filter read as a LIST, for a multi-select. A single stored string is a list of one. */
export const manyFilters = (value: string | string[] | undefined): string[] =>
	value === undefined ? [] : Array.isArray(value) ? value : [value];

/** The filter bar's shape — key → chosen values — read off a listing query.
 *
 *  Only the keys given, so a listing whose bar covers three of its five filters leaves the other
 *  two alone, and a key holding nothing is absent rather than an empty array (the bar shows a
 *  filter it can see a value for).
 */
export function barValueFrom(
	query: ListingQuery | null | undefined,
	keys: readonly string[],
	rangeKeys: readonly string[] = []
): Record<string, string[]> {
	const out: Record<string, string[]> = {};
	for (const key of keys) {
		const raw = manyFilters(query?.filters[key]);
		if (rangeKeys.includes(key)) {
			const pair = rangePair(raw);
			if (pair) out[key] = pair;
			continue;
		}
		const values = raw.filter((v) => v && v !== ALL);
		if (values.length) out[key] = values;
	}
	return out;
}

/** A `[from, to]` pair kept POSITIONALLY — the twin of `app/listing.py::clean_range`. */
function rangePair(raw: readonly string[]): string[] | null {
	const pair = [raw[0] ?? '', raw[1] ?? ''].map((v) => (v === ALL ? '' : v.trim()));
	return pair[0] || pair[1] ? pair : null;
}

/** The bar's change, applied to a listing query as ONE patch.
 *
 *  One patch and not a `setFilter` per key, because the bar changes several at once — Reset clears
 *  the lot — and two navigations would page the listing twice and leave it briefly showing a state
 *  nobody asked for. Returns to page one: page 4 of a two-page result is an empty table.
 */
export function withBarValue(
	query: ListingQuery,
	keys: readonly string[],
	next: Record<string, string[]>,
	rangeKeys: readonly string[] = []
): ListingQuery {
	const filters = { ...query.filters };
	for (const key of keys) {
		if (rangeKeys.includes(key)) {
			const pair = rangePair(next[key] ?? []);
			if (pair) filters[key] = pair;
			else delete filters[key];
			continue;
		}
		const values = (next[key] ?? []).filter((v) => v && v !== ALL);
		if (values.length) filters[key] = values;
		else delete filters[key];
	}
	return { ...query, filters, offset: 0 };
}

/** True when anything is narrowing the list — drives the Clear control. */
export const isFiltered = (query: ListingQuery): boolean =>
	!!query.q.trim() ||
	Object.values(query.filters).some((v) => (Array.isArray(v) ? v.length > 0 : !!v));

/**
 * The 1-indexed page a query is on — the offset↔page translation the API (offset/limit) and the
 * pager (page numbers) disagree about. One place for it, so a listing's current-page marker and its
 * "26–50 of 798" label cannot drift: the dashboard's table once kept its own `page = 1` and only
 * the label followed the URL.
 */
export function pageOf(query: Pick<ListingQuery, 'offset' | 'limit'>): number {
	return Math.floor(Math.max(0, query.offset) / Math.max(1, query.limit)) + 1;
}

/** "1–50 of 1,340", or "No matches". The label a pager reads out. */
export function rangeLabel(query: ListingQuery, total: number, shown: number): string {
	return rangeLabelFromOffset(query.offset, shown, total);
}

/**
 * The "1–50 of 1,340" formatting itself, taking a plain offset rather than a whole
 * `ListingQuery` — for a caller that paginates a page NUMBER against an already-loaded array
 * (not a server-driven offset/limit query) and would otherwise have to hand-roll a second copy
 * of this exact string (same en-dash, same `toLocaleString('en-GB')` on every number, same
 * "No matches" empty wording) just to avoid constructing a `ListingQuery` it has no other use
 * for. `rangeLabel` above is the offset-query-shaped wrapper most callers want.
 */
export function rangeLabelFromOffset(offset: number, shown: number, total: number): string {
	if (total === 0) return 'No matches';
	// One page: the range IS the total, and "1–16 of 16" says the same thing twice. This matters now
	// the label shows even on a single page — which it does so a short list is distinguishable from
	// the first page of a long one.
	if (offset === 0 && shown >= total) {
		return `${total.toLocaleString('en-GB')} ${total === 1 ? 'result' : 'results'}`;
	}
	const first = offset + 1;
	const last = offset + shown;
	return `${first.toLocaleString('en-GB')}–${last.toLocaleString('en-GB')} of ${total.toLocaleString('en-GB')}`;
}

/**
 * Filter values -> select options, using the server's facet list.
 *
 * The options come from the server because they must describe the whole match set: derived from
 * the rows on screen, a filter could only ever offer values already visible, so narrowing to
 * page 2 would change what you are allowed to filter by.
 */
/** A range filter's `min`/`max` from the API's bounds, rounded OUTWARDS so the track contains
 *  every row it is drawn over — otherwise the largest sits past the end, out of the handle's reach. */
export function rangeFromBounds(
	bounds: Record<string, number[]> | undefined,
	key: string
): { min: string; max: string } | undefined {
	const lo = bounds?.[key]?.[0];
	const hi = bounds?.[key]?.[1];
	if (lo === undefined || hi === undefined) return undefined;
	if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return undefined;
	return { min: String(Math.floor(lo)), max: String(Math.ceil(hi)) };
}

export function optionsFromFacet(
	facets: Record<string, string[]> | undefined,
	key: string,
	label: (value: string) => string = (v) => v,
	counts?: Record<string, Record<string, number>>
): { value: string; label: string; count?: number }[] {
	return (facets?.[key] ?? []).map((value) => ({
		value,
		label: label(value),
		count: counts?.[key]?.[value]
	}));
}

/**
 * The URL for a 1-indexed page, as a query-only href.
 *
 * Query-only (`?a=1&b=2`) so it resolves against whatever path it is rendered on — the pager does
 * not need to know the route. `base` is the page's CURRENT params, so unrelated ones (a `?focus=`
 * deep link) survive being paged past rather than being dropped by the link.
 */
export function pageHref(query: ListingQuery, page: number, base?: URLSearchParams): string {
	const offset = Math.max(0, (page - 1) * query.limit);
	const params = paramsFromQuery({ ...query, offset }, base);
	const qs = params.toString();
	return qs ? `?${qs}` : '?';
}
