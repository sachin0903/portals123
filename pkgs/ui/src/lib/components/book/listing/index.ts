// The shared listing frame for the book-sized tables (clients, accounts, transactions):
// the search/filter toolbar and the pure predicates behind it. Lives under book/ because the
// portal waterfall renders these listings in BOTH the platform portal and the admin portal.
//
// Deliberately NOT re-exported from the package root: `ALL`/`matches`/`search` are far too
// generic for the `@graphene/ui` namespace. Consumers import the subpath
// `@graphene/ui/book/listing` (see pkgs/ui package.json `exports`).
export { default as ListingToolbar } from './listing-toolbar.svelte';
export { default as ListingOverflow } from './listing-overflow.svelte';
export { default as FilterSelect } from './filter-select.svelte';
// `ListingToolbar` renders the bar from `filterDefs`/`filterValue`. The panel is the same
// editors, stacked, for a surface that wants every field in one place. Both are exported for
// the surfaces that compose their own frame.
export { default as FilterPanel } from './filter-panel.svelte';
export type { FilterDef } from './filter-panel.svelte';
export { default as FilterBar } from './filter-bar.svelte';
export {
	appliedFilters,
	countFilters,
	handlesToPair,
	niceRange,
	sameFilters,
	withinRange,
	type AppliedFilter,
	type FilterVocabulary
} from './applied.js';
export { default as ExportMenu } from './export-menu.svelte';
// Type only: the export route builds this, the menu draws it. jsPDF itself loads on demand.
export type { ExportTable } from './table-pdf.js';
export { default as DateRangeFilter } from './date-range-filter.svelte';
export { toCalendarDate, toDateText } from './date-range.js';
export { default as Pagination } from './pagination.svelte';
export { default as PaginationStrip } from './pagination-strip.svelte';
export {
	ALL,
	matches,
	matchesAny,
	search,
	optionsFrom,
	mergeOptions,
	humanise,
	SEARCH_BOX_CLASS
} from './filters.js';
// One status -> tone map for every book listing, so "active" cannot look healthy on one
// surface and neutral on the next.
export { statusTone } from './status-tone.js';

// Putting a listing's question in the URL, so the LOADER can read it — the mechanics every
// server-narrowed listing repeats.
export { nextUrl, nextUrlFromStart, type UrlPatch } from './url.js';
// The server-driven half: the query object, its URL round-trip, and the facet-backed options.
export {
	PAGE_SIZE,
	apiParams,
	emptyQuery,
	isFiltered,
	oneFilter,
	manyFilters,
	barValueFrom,
	withBarValue,
	itemsOf,
	optionsFromFacet,
	rangeFromBounds,
	pageHref,
	pageOf,
	paramsFromQuery,
	queryFromParams,
	rangeLabel,
	rangeLabelFromOffset,
	type ListingPage,
	type ListingQuery
} from './query.js';

// Ordering for a click that changes the URL twice (see nav.ts).
export { createListingNav, type ListingNav } from './nav.js';
