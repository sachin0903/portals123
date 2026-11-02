import type { ListingSort } from '$lib/listing-export';

/** Adviser is deliberately absent: this route is already scoped to one client. */
// `account` is here because a client's ledger spans several: "which of their accounts" is the
// question this page gets asked. The firm listing does not offer it — search already reaches an
// account by name there — and an account's own ledger has one.
//
// Exported here rather than declared twice: the loader and the export both import this one list,
// so they cannot quietly part ways on what counts as a filter.
export const FILTER_KEYS = [
	'status',
	'custodian',
	'type',
	'account',
	'date_from',
	'date_to',
	'amount',
	'quantity',
	'currency'
] as const;

/** The sort the loader applies when the URL names none — the export must reach the API with the
 *  same default, or an unsorted file disagrees with the table's own row order. */
export const SORT: ListingSort = { sort: 'date', dir: 'desc' };
