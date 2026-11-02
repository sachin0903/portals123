/**
 * Shared helpers for the book-sized listings (clients, accounts, transactions).
 *
 * Filtering is client-side for the same reason sorting is: these lists are a firm's book,
 * already fetched in one call. When one outgrows that, `matches` becomes the server query and
 * the page components don't change.
 */

/** Sentinel for "no filter applied" — Bits' Select needs a real value for the unfiltered state. */
export const ALL = '__all__';

/** True when the filter is unset or the row's value matches it. */
export function matches(filter: string | string[], value: string | null | undefined): boolean {
	const text = value ?? '';
	// A LIST is an OR, and an EMPTY list is no filter — a multi-select the reader emptied means
	// "show everything", the same as never having set it. The Python twin says the same
	// (`app/listing.py::matches`), because the two run over the same rows on different sides.
	if (Array.isArray(filter)) return filter.length === 0 || filter.some((f) => text === f);
	return filter === ALL || text === filter;
}

/** True when the filter is unset or any of the row's values match it (multi-valued columns). */
export function matchesAny(
	filter: string | string[],
	values: readonly (string | null | undefined)[]
): boolean {
	const texts = values.map((v) => v ?? '');
	// Both sides may be several: a client advised by Cole and Shaw matches a filter of Shaw or Vale.
	if (Array.isArray(filter))
		return filter.length === 0 || filter.some((f) => texts.some((t) => t === f));
	return filter === ALL || texts.some((t) => t === filter);
}

/** Case-insensitive "contains" across a row's searchable text. Empty query matches everything. */
export function search(query: string, ...fields: (string | null | undefined)[]): boolean {
	const needle = query.trim().toLowerCase();
	if (!needle) return true;
	return fields.some((f) => (f ?? '').toLowerCase().includes(needle));
}

/**
 * Distinct non-empty values as select options, collated for humans. Built from the rows on
 * screen so a filter can never offer a value that would return nothing.
 */
export function optionsFrom<T>(
	rows: T[],
	read: (row: T) => string | null | undefined | (string | null | undefined)[],
	label: (value: string) => string = (v) => v
): { value: string; label: string }[] {
	const seen = new Set<string>();
	for (const row of rows) {
		const raw = read(row);
		for (const value of Array.isArray(raw) ? raw : [raw]) {
			if (value) seen.add(value);
		}
	}
	return [...seen]
		.sort((a, b) => a.localeCompare(b, 'en-GB', { numeric: true, sensitivity: 'base' }))
		.map((value) => ({ value, label: label(value) }));
}

/** The API's facets plus what only the rows on screen know about — the contributions queue
 *  prepends separately-fetched credits, and with facets alone a firm with nothing booked showed
 *  "No matches." above a full table. `first` wins on label and count, and keeps its order. */
export function mergeOptions(
	first: { value: string; label: string; count?: number }[],
	second: { value: string; label: string; count?: number }[]
): { value: string; label: string; count?: number }[] {
	const known = new Set(first.map((option) => option.value));
	return [...first, ...second.filter((option) => !known.has(option.value))];
}

/** Re-exported, not reimplemented: the same namer serves the generic chrome (`member-directory`,
 *  `auth-profile`) which cannot import a book module, and the estate had two copies of this that
 *  had already diverged on which separators they split. See `lib/sentence-case.ts`. */
export { humanise } from '../../../sentence-case.js';

/** How wide a search box may get: full width where the row stacks, 24rem from `sm` up.
 *
 *  ONE constant because the cap kept being decided per call site and the ones outside
 *  `ListingToolbar` never got it — a `flex-1` box on a page the shell does not cap runs the whole
 *  width of a wide monitor, so a one-word query sits in a metre of empty input and the controls
 *  beside it are pushed to the far edge. A search box is not a form field; it does not want the
 *  row it is in. */
export const SEARCH_BOX_CLASS = 'w-full sm:w-96';

/** A filter vocabulary shorter than this is read in place. At this length it gets a search box. */
export const FILTER_OPTION_SEARCH_AT = 8;

/** The chip a chosen filter wears, and the button inside it. Shared so a date chip and a
 *  status chip are the same object. `bg-card` lifts them off the filter row, which is `--muted`
 *  in light and `--background` in dark — muted chips would vanish into one of those and float
 *  above the other. */
export const FILTER_TAG_CLASS =
	'border-border/70 bg-card inline-flex max-w-full items-center rounded-full border text-xs';
export const FILTER_TAG_TRIGGER_CLASS =
	'hover:bg-muted/60 flex min-w-0 cursor-pointer items-center gap-1 rounded-full py-0.5 pr-1 pl-2.5 text-left outline-none focus-visible:ring-2';
