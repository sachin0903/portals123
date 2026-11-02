import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type OrgRole, type Permission } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** The tenant that owns the core project (Graphene) — where ops roles/staff live. */
async function opsTenantUid(accessToken: string | null, fetch: typeof globalThis.fetch) {
	const res = await fetch(`${apiUrl()}/admin/operations`, { headers: authHeaders(accessToken) });
	if (!res.ok) throw new Error(await detail(res));
	// The uid, not the serial: `/admin/tenants/{tenant_uid}/…` is what these pages call.
	return (await res.json()).tenant_uid as string;
}

const writeRefusal = (locals: { permissions: string[]; role: string | null }, action: string) =>
	can(locals.permissions, locals.role, 'operations:write')
		? null
		: fail(403, {
				action,
				error: 'You need the operations:write permission to manage operations roles.'
			});

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'operations:read')) error(403, 'Not authorised');
	const canManage = can(locals.permissions, locals.role, 'operations:write');
	const headers = authHeaders(locals.accessToken);
	const uid = await opsTenantUid(locals.accessToken, fetch);
	const [rolesRes, permsRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${uid}/roles`, { headers }),
		fetch(`${apiUrl()}/admin/permissions`, { headers })
	]);
	const roles: OrgRole[] = rolesRes.ok ? await rolesRes.json() : [];
	const permissions: Permission[] = permsRes.ok ? await permsRes.json() : [];
	// Internal Operations is a single portal — show only its roles + permissions.
	return {
		roles: roles.filter((r) => r.portal === 'operations'),
		permissions: permissions.filter((p) => p.portal === 'operations'),
		canManage
	};
};

export const actions: Actions = {
	createRole: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'createRole');
		if (refused) return refused;
		const data = await request.formData();
		const name = String(data.get('name') ?? '').trim();
		const slug = String(data.get('slug') ?? '').trim();
		if (!name || !slug)
			return fail(400, { action: 'createRole', error: 'Name and slug are required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/roles`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				name,
				slug,
				portal: 'operations',
				permissions: data.getAll('permissions').map(String)
			})
		});
		if (!res.ok) return fail(res.status, { action: 'createRole', error: await detail(res) });
		return { action: 'createRole', ok: true };
	},

	setRolePermissions: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'setRolePermissions');
		if (refused) return refused;
		const data = await request.formData();
		const slug = String(data.get('slug') ?? '');
		if (!slug) return fail(400, { action: 'setRolePermissions', error: 'A role is required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/roles/${slug}/permissions`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ permissions: data.getAll('permissions').map(String) })
		});
		if (!res.ok)
			return fail(res.status, { action: 'setRolePermissions', error: await detail(res) });
		return { action: 'setRolePermissions', ok: true, slug };
	}
};
