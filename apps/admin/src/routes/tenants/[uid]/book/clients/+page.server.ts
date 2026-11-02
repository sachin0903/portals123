import { redirect } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { bookUrl, getJson, type BookClientRow } from '$lib/book/api';
import { apiParams, itemsOf, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { PageServerLoad } from './$types';

const FILTER_KEYS = ['status', 'adviser', 'custodian', 'type', 'account_type', 'value'] as const;

/**
 * The firm's clients, read as its own read-all staff would (the platform's `/clients` loader with
 * `?tenant=` on every call — see `$lib/book/api`). Same shape decisions as the platform:
 *  · the closed set is fetched alongside the live book so the Closed filter is visible;
 *  · a failure is a FLAG, never an empty list — "No clients yet" over a 500 is the worst render;
 *  · one `valuesAsOf` stamp across the book, because the list is one snapshot.
 * No `actions` are exported: this surface mounts read-only, restore/create belong to the firm.
 */
export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const headers = authHeaders(locals.accessToken);
	// The left column — see `ListingQuery.defaultSort`.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'name', dir: 'asc' });
	const archivedView =
		url.searchParams.get('segment') === 'closed' || url.searchParams.get('segment') === 'archived';
	const liveParams = Object.fromEntries(apiParams(query));
	const [live, archived] = await Promise.all([
		getJson<ListingPage<BookClientRow>>(
			fetch,
			bookUrl('/crm/contacts', tenant.uid, liveParams),
			headers
		),
		archivedView
			? getJson<ListingPage<BookClientRow>>(
					fetch,
					bookUrl('/crm/contacts', tenant.uid, { archived: 'true', limit: '200' }),
					headers
				)
			: Promise.resolve({
					ok: true as const,
					data: { items: [] as BookClientRow[], total: 0, facets: {} }
				})
	]);
	const page: ListingPage<BookClientRow> = live.ok
		? Array.isArray(live.data)
			? { items: live.data, total: live.data.length, facets: {} }
			: live.data
		: { items: [], total: 0, facets: {} };
	const clients = page.items;
	const valuesAsOf = clients.reduce<string | null>(
		(latest, c) => (c.value_as_of && (!latest || c.value_as_of > latest) ? c.value_as_of : latest),
		null
	);
	const archivedRows = archived.ok ? itemsOf(archived.data) : [];
	return {
		clients,
		matchCount: page.total,
		facets: page.facets,
		facetCounts: page.facet_counts ?? {},
		// The slider track — see `ListingPage.bounds`.
		bounds: 'bounds' in page ? (page.bounds ?? {}) : {},
		query,
		archived: archivedRows,
		valuesAsOf,
		loadError: !live.ok,
		archivedError: !archived.ok
	};
};
