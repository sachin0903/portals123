import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type TenantSummary } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** One window of firms. Sized to fill the list several times over — the page scrolls rather than
 *  pages, so this is what loads before you scroll, not what exists. */
const PAGE_SIZE = 200;

/**
 * The firms, SEARCHED BY THE API.
 *
 * The page used to fetch every firm and filter them in the browser. That searches the batch you
 * were sent and reports it as the answer for the estate — "no firms match" when the truth is
 * "none in this batch". `q` lives in the URL so a search survives a reload and can be linked.
 */
export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'tenants:read')) error(403, 'Not authorised');

	const query = url.searchParams.get('q')?.trim() ?? '';
	const search = new URLSearchParams({ limit: String(PAGE_SIZE), offset: '0' });
	if (query) search.set('q', query);

	let tenants: TenantSummary[] = [];
	let total = 0;
	let apiError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/admin/tenants?${search}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			const page: { items: TenantSummary[]; total: number } = await res.json();
			tenants = page.items;
			total = page.total;
		} else if (res.status === 403)
			apiError = 'Your account needs a Graphene system/admin role to manage tenants.';
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	return {
		tenants,
		total,
		query,
		apiError,
		canManage: can(locals.permissions, locals.role, 'tenants:write')
	};
};

export const actions: Actions = {
	createTenant: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		const name = String(data.get('name') ?? '').trim();
		const alias = String(data.get('alias') ?? '').trim();
		// Checkbox: present => clone the model tenant's roles; absent => a blank tenant.
		const fromModel = data.get('from_model') !== null;
		if (!name || !alias) return fail(400, { error: 'Name and alias are both required.' });

		const res = await fetch(`${apiUrl()}/admin/tenants`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ name, alias, from_model: fromModel })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created = await res.json();
		// The UID as well as the id. The tenant section is addressed by uid (`routes/tenants/[uid]`)
		// and this action returned only `id`, so the page navigated to `/tenants/undefined` — the
		// half of the uid migration that lives in a form action rather than in a route.
		// `POST /admin/tenants` has always returned both (`RETURNING id, uid, …`).
		return { ok: true, id: created.id as number, uid: created.uid as string };
	}
};
