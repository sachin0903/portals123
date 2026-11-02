import type { ListingSort } from '$lib/listing-export';
import { _TRANSACTION_FILTERS } from '../+layout.server';

/** The band's filter keys — the record layout's own list (shared with its title/subtitle work),
 *  re-exported here so the loader and the export import one name rather than two import paths
 *  for the same list. */
export const FILTER_KEYS = _TRANSACTION_FILTERS;

/** The sort the loader applies when the URL names none — the export must reach the API with the
 *  same default, or an unsorted file disagrees with the table's own row order. */
export const SORT: ListingSort = { sort: 'date', dir: 'desc' };
