import { redirect } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { bookUrl, getJson } from '$lib/book/api';
import type { Account } from '@graphene/ui';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { PageServerLoad } from './$types';

/** The filters this listing understands — the platform's own list, since it is the same table. */
const FILTER_KEYS = [
	'status',
	'adviser',
	'custodian',
	'type',
	'service',
	'model',
	'value'
] as const;

/**
 * The firm's accounts — the platform's `/accounts` loader with `?tenant=`.
 *
 * THE DEFECT. This mounted the SAME shared listing as the platform's own page, which searches and
 * pages through the API — but fetched `/crm/accounts` with no query at all and let the component
 * fall back to filtering in the browser. One table, two behaviours: an operator searching a firm's
 * book was searching only the rows that happened to be sent, and "no account for Halloway" meant
 * "not in this batch" while reading as "this firm has no such account".
 *
 * The endpoint already took `q`/filters/paging, so this is the loader catching up with it.
 *
 * The shared listing has no error banner prop yet, so a failed read renders as an empty book — the
 * same trade the platform page makes today; fix both when the component grows the flag.
 */
export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	// The left column — see `ListingQuery.defaultSort`.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'name', dir: 'asc' });
	// Through `bookUrl`'s own params rather than concatenated on the end: it already owns the
	// `?tenant=` every book call carries, and appending a second query string is how one of them
	// ends up with a stray `?`.
	const res = await getJson<ListingPage<Account>>(
		fetch,
		bookUrl('/crm/accounts', tenant.uid, Object.fromEntries(apiParams(query))),
		authHeaders(locals.accessToken)
	);
	const page = res.ok ? res.data : { items: [], total: 0, facets: {} };
	return {
		query,
		accounts: page.items,
		matchCount: page.total,
		facets: page.facets ?? {},
		// The slider track — see `ListingPage.bounds`.
		bounds: page.bounds ?? {}
	};
};
