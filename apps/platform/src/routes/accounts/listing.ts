/** The same filters the listing understands — kept in step with the loader's own list. */
export const FILTER_KEYS = [
	'status',
	'adviser',
	'custodian',
	'type',
	'service',
	'model',
	'value'
] as const;

/** The sort the loader applies when the URL names none. The export must use the SAME object —
 *  see `exportParams` — or a URL with no `sort` gives the file the API's own order instead of
 *  the table's. */
export const SORT = { sort: 'name', dir: 'asc' } as const;
