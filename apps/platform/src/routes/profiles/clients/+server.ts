import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

const TYPEAHEAD_LIMIT = 20;

type Contact = { uid: string; name: string | null; email: string | null };

/** Create-dialog typeahead for an existing client. First paint of `/profiles` does not fetch
 *  the contact catalogue (`limit=500` used to ride the directory). */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const canManage =
		can(locals.permissions, locals.role, 'profiles:write') ||
		can(locals.permissions, locals.role, 'members:write');
	if (!canManage || !can(locals.permissions, locals.role, 'contacts:write')) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const search = new URLSearchParams({ limit: String(TYPEAHEAD_LIMIT) });
	const q = url.searchParams.get('q')?.trim() ?? '';
	if (q) search.set('q', q);
	const res = await fetch(`${apiUrl()}/crm/contacts?${search}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	const body: unknown = await res.json();
	const items = (
		Array.isArray(body) ? body : ((body as { items?: Contact[] }).items ?? [])
	) as Contact[];
	return json(
		items.map((client) => ({
			value: client.uid,
			label: client.name ?? client.email ?? 'Unnamed client',
			hint: client.email ?? undefined
		}))
	);
};
