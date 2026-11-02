import { redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders } from '$lib/api';
import type { PageServerLoad } from './$types';

export { catalogueActions as actions } from '$lib/catalogue/actions';

// This registry is the one listing that cannot be loaded whole: Seccl publishes 929 ceding
// schemes, so it pages and sorts on the SERVER. The layout loader still supplies the rest of the
// catalogue (products, for the link editor) — only the rows come from here.
export const load: PageServerLoad = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The numeric id the `/admin/tenants/{tenant_id}` endpoints take. Resolved once by the
	// [uid] layout, so this costs no extra round trip.
	const page = Math.max(1, Number(url.searchParams.get('page') ?? 1) || 1);
	const sort = url.searchParams.get('sort') ?? 'name';
	const dir: 'asc' | 'desc' = url.searchParams.get('dir') === 'desc' ? 'desc' : 'asc';
	// Filters go to the SERVER, so they narrow the register rather than the page on screen.
	// `product`/`rail` are MULTI-VALUED — the API ORs a repeated `?product=ISA&product=SIPP`.
	const q = url.searchParams.get('q') ?? '';
	const product = url.searchParams.getAll('product').filter(Boolean);
	const rail = url.searchParams.getAll('rail').filter(Boolean);
	const query = new URLSearchParams({
		limit: String(PAGE_SIZE),
		offset: String((page - 1) * PAGE_SIZE),
		sort,
		dir
	});
	if (q) query.set('q', q);
	for (const p of product) query.append('product', p);
	for (const r of rail) query.append('rail', r);
	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/catalogue/providers/transfer?${query}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	// A failed page keeps the shell rather than blanking the route: total 0 renders the empty
	// state, which is honest about there being nothing to show right now.
	const body = res.ok ? await res.json() : { items: [], total: 0, facets: null };
	return {
		transferProviders: body.items,
		total: body.total,
		// What the register actually contains — computed over the whole thing, so narrowing never
		// removes the option that would widen the search again.
		facets: (body.facets ?? { products: [], rails: [] }) as {
			products: string[];
			rails: string[];
		},
		page,
		pageSize: PAGE_SIZE,
		sort,
		dir,
		q,
		product,
		rail
	};
};
