import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Instrument } from '$lib/catalogue/types';
import type { PageServerLoad } from './$types';

// The shared catalogue writes, as every other section under /platform uses.
export { catalogueActions as actions } from '$lib/catalogue/actions';

/** How many rows one window holds. The table is a fixed-height scrolling frame, so this is sized
 *  to fill it several times over — enough that scrolling feels like a list rather than a pager,
 *  small enough that the first paint is not the whole universe. */
const PAGE_SIZE = 200;

/** The filters this listing understands, named so an unrelated query param is never forwarded. */
const FILTER_KEYS = ['asset_type', 'currency', 'exchange_code'] as const;

/**
 * The instrument universe, searched and windowed BY THE SERVER.
 *
 * Every other page in this section reads `../+layout.server.ts`'s bundled catalogue, and this one
 * used to as well — but that bundle's `instruments` key is the PICKER's slice, the first 500 by
 * name (`catalogue.instruments`). `platform.instruments` is global and un-scoped (0041: an ISIN is
 * not tenant data), so it holds every tenant's securities: SEI's first sync alone landed 6,170.
 * Searching the bundled slice in the browser therefore answered "no assets match that search" for
 * any security outside those first 500 — which on a page whose whole job is "do we hold this?"
 * reads as no, rather than as a page that did not look.
 *
 * So this page gets its own loader against `/catalogue/instruments`, which searches and windows in
 * SQL. `q` lives in the URL, so a search survives a reload and can be linked.
 */
export const load: PageServerLoad = async ({ params, locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');

	const q = url.searchParams.get('q')?.trim() ?? '';
	// The three columns the universe can be narrowed by. Server-side like the search and for the
	// same reason: the browser only ever holds one window, so a filter applied here would answer
	// "no USD securities" when it means "none in these 200".
	const filters = Object.fromEntries(
		FILTER_KEYS.map((key) => [
			key,
			url.searchParams
				.getAll(key)
				.map((v) => v.trim())
				.filter(Boolean)
		])
	) as Record<(typeof FILTER_KEYS)[number], string[]>;

	const search = new URLSearchParams({ limit: String(PAGE_SIZE), offset: '0' });
	if (q) search.set('q', q);
	for (const key of FILTER_KEYS) for (const value of filters[key]) search.append(key, value);

	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/catalogue/instruments?${search}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (res.status === 404) error(404, 'Tenant not found.');
	if (!res.ok) error(res.status, await detail(res));
	const page: { items: Instrument[]; total: number; facets?: Record<string, string[]> } =
		await res.json();

	return {
		instruments: page.items,
		instrumentsTotal: page.total,
		query: q,
		filters,
		// Computed by the API over the match set with each filter's own clause lifted, so choosing
		// "USD" does not leave USD as the only currency on offer. Derived from the rows on screen
		// they could only ever describe this window.
		facets: page.facets ?? {},
		pageSize: PAGE_SIZE
	};
};
