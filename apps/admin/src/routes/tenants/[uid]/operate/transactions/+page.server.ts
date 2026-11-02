import { redirect } from '@sveltejs/kit';
import type { Transaction } from '@graphene/ui';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

/** The platform's `/transactions` filters, date range included. */
const FILTER_KEYS = [
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

export const load: PageServerLoad = async ({ locals, fetch, url, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'date', dir: 'desc' });
	return { query, book: loadBook(fetch, locals.accessToken, tenant.uid, query) };
};

async function loadBook(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	query: ReturnType<typeof queryFromParams>
) {
	const result = await targetJson<ListingPage<Transaction>>(
		fetch,
		accessToken,
		tenantUid,
		'/crm/transactions',
		new URLSearchParams(`paged=true&${apiParams(query)}`)
	);
	const page = result.ok ? result.data : { items: [], total: 0, facets: {} };
	return {
		transactions: page.items,
		matchCount: page.total,
		facets: page.facets,
		bounds: page.bounds ?? {},
		loadError: !result.ok
	};
}
