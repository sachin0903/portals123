import type { ListingSort } from '$lib/listing-export';

/** The filters this listing understands. Named so an unrelated query param is not forwarded.
 *
 *  Exported here rather than declared twice: the loader and the export both import this one
 *  list, so they cannot quietly part ways on what counts as a filter. */
// `date_from`/`date_to` are applied in SQL rather than through the facet registry, but they are
// still listing filters: they belong in the URL, travel with the page number, and reach the export.
export const FILTER_KEYS = [
	'status',
	'adviser',
	'custodian',
	'type',
	'date_from',
	'date_to',
	'amount',
	'quantity',
	'currency'
] as const;

/** The sort the loader applies when the URL names none — the export must reach the API with the
 *  same default, or an unsorted file disagrees with the table's own row order. */
export const SORT: ListingSort = { sort: 'date', dir: 'desc' };
