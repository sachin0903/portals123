import type { ListingSort } from '$lib/listing-export';

/** The filters this listing understands. Named so an unrelated query param (`?segment=closed`)
 *  is never mistaken for one and forwarded to the API. Shared by the page and its export, so the
 *  file can never be narrowed differently from the table. */
export const FILTER_KEYS = [
	'status',
	'adviser',
	'custodian',
	'type',
	'account_type',
	'value'
] as const;

/** The left column, as every other paged listing — see `ListingQuery.defaultSort`. */
export const SORT: ListingSort = { sort: 'name', dir: 'asc' };
