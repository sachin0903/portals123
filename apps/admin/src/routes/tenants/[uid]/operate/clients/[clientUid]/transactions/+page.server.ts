import { redirect } from '@sveltejs/kit';
import type { Transaction } from '@graphene/ui';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

const FILTERS = ['status', 'type', 'account', 'date_from', 'date_to'] as const;

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, detail } = await parent();
	const query = queryFromParams(url.searchParams, FILTERS, { sort: 'date', dir: 'desc' });
	const result = await targetJson<ListingPage<Transaction>>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/crm/transactions',
		{ contact_id: detail.contact.id, paged: true, ...Object.fromEntries(apiParams(query)) }
	);
	const page = result.ok ? result.data : { items: [], total: 0, facets: {} };
	return {
		transactions: page.items,
		matchCount: page.total,
		facets: page.facets,
		bounds: page.bounds ?? {},
		query,
		loadError: !result.ok
	};
};
