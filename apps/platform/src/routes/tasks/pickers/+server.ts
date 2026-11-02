import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson, type Member } from '$lib/api';
import type { ClientOption } from '@graphene/ui/book/documents';
import type { RequestHandler } from './$types';

type RoleOption = { slug: string; name: string; portal: string | null };

/** Assignee catalogues for the New-task dialog. Loaded when the dialog opens so the inbox
 *  first paint is only the two task windows. A caller without members:read still gets roles
 *  and clients — the picker degrades rather than disappearing. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'tasks:write')) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const h = authHeaders(locals.accessToken);
	const [members, roles, clients] = await Promise.all([
		fetchJson<Member[]>(fetch, `${apiUrl()}/firm/members?kind=staff`, h),
		fetchJson<RoleOption[]>(fetch, `${apiUrl()}/firm/roles`, h),
		fetchJson<ClientOption[]>(fetch, `${apiUrl()}/documents/clients`, h)
	]);
	return json({
		members: members.ok ? members.data.filter((m) => m.enabled) : [],
		roles: roles.ok ? roles.data.filter((r) => r.portal !== 'client') : [],
		clients: clients.ok ? clients.data : []
	});
};
