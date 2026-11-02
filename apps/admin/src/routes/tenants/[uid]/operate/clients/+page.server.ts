import { redirect } from '@sveltejs/kit';
import type { BookClientRow } from '$lib/book/api';
import { apiParams, itemsOf, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

/** The platform's `/clients` filters. `?segment=closed` is the population, not a filter. */
const FILTER_KEYS = ['status', 'adviser', 'custodian', 'type', 'account_type', 'value'] as const;

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'name', dir: 'asc' });
	const book = loadBook(fetch, locals.accessToken, tenant.uid, url, query);
	return {
		query,
		book
	};
};

async function loadBook(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	url: URL,
	query: ReturnType<typeof queryFromParams>
) {
	// `archived` is the old spelling; links carrying it still land on the Closed population.
	const segment = url.searchParams.get('segment');
	const archivedView = segment === 'closed' || segment === 'archived';
	const [live, archived] = await Promise.all([
		targetJson<ListingPage<BookClientRow> | BookClientRow[]>(
			fetch,
			accessToken,
			tenantUid,
			'/crm/contacts',
			apiParams(query)
		),
		archivedView
			? targetJson<ListingPage<BookClientRow> | BookClientRow[]>(
					fetch,
					accessToken,
					tenantUid,
					'/crm/contacts',
					{ archived: true, limit: 200 }
				)
			: null
	]);
	const page = live.ok ? live.data : { items: [], total: 0, facets: {} };
	return {
		clients: itemsOf(page),
		matchCount: Array.isArray(page) ? page.length : page.total,
		facets: Array.isArray(page) ? {} : page.facets,
		facetCounts: Array.isArray(page) ? {} : (page.facet_counts ?? {}),
		bounds: Array.isArray(page) ? {} : (page.bounds ?? {}),
		archived: archived?.ok ? itemsOf(archived.data) : [],
		valuesAsOf: itemsOf(page).reduce<string | null>(
			(latest, client) =>
				client.value_as_of && (!latest || client.value_as_of > latest)
					? client.value_as_of
					: latest,
			null
		),
		loadError: !live.ok,
		archivedError: archived != null && !archived.ok
	};
}
