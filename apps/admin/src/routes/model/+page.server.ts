import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type OrgRole, type Permission } from '$lib/api';
import { can } from '$lib/permissions';
import { modelTenant } from '$lib/model';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'model:read')) error(403, 'Not authorised');
	const canManage = can(locals.permissions, locals.role, 'model:write');
	const headers = authHeaders(locals.accessToken);
	const { uid } = await modelTenant(locals.accessToken, fetch);
	const [rolesRes, permsRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${uid}/roles`, { headers }),
		fetch(`${apiUrl()}/admin/permissions`, { headers })
	]);
	const roles: OrgRole[] = rolesRes.ok ? await rolesRes.json() : [];
	const permissions: Permission[] = permsRes.ok ? await permsRes.json() : [];
	// modelTenantUid is surfaced so the page can deep-link into the shared per-tenant email surface
	// (/tenants/{uid}/email-templates, UID-addressed) rather than keep a model-only copy of it.
	return { roles, permissions, canManage, modelTenantUid: uid };
};

export const actions: Actions = {
	createRole: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		const name = String(data.get('name') ?? '').trim();
		const slug = String(data.get('slug') ?? '').trim();
		const portal = String(data.get('portal') ?? '');
		if (!name || !slug)
			return fail(400, { action: 'createRole', error: 'Name and slug are required.' });
		if (portal !== 'client' && portal !== 'platform')
			return fail(400, { action: 'createRole', error: 'A portal is required.' });
		const { uid } = await modelTenant(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/roles`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				name,
				slug,
				portal,
				permissions: data.getAll('permissions').map(String)
			})
		});
		if (!res.ok) return fail(res.status, { action: 'createRole', error: await detail(res) });
		return { action: 'createRole', ok: true };
	},

	setRolePermissions: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		const slug = String(data.get('slug') ?? '');
		if (!slug) return fail(400, { action: 'setRolePermissions', error: 'A role is required.' });
		const { uid } = await modelTenant(locals.accessToken, fetch);
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
