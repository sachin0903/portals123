import { json } from '@sveltejs/kit';
import type { ClientOption } from '@graphene/ui/book/documents';
import { canOperateWrite, targetJson } from '$lib/operate';
import type { RequestHandler } from './$types';

type Member = { user_id: string; email: string; name: string | null; enabled: boolean };
type Role = { slug: string; name: string; portal: string | null };

/** Assignee catalogues for the New-task dialog. Loaded when the dialog opens. */
export const GET: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!canOperateWrite(locals)) return json({ error: 'Not permitted.' }, { status: 403 });
	const [members, roles, clients] = await Promise.all([
		targetJson<Member[]>(fetch, locals.accessToken, params.uid, '/firm/members', {
			kind: 'staff'
		}),
		targetJson<Role[]>(fetch, locals.accessToken, params.uid, '/firm/roles'),
		targetJson<ClientOption[]>(fetch, locals.accessToken, params.uid, '/documents/clients')
	]);
	return json({
		members: members.ok ? members.data.filter((member) => member.enabled) : [],
		roles: roles.ok ? roles.data.filter((role) => role.portal !== 'client') : [],
		clients: clients.ok ? clients.data : []
	});
};
