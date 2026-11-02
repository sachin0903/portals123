import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type OrgRole, type Permission } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/**
 * Server-side write guard: the page is reachable with tenants:read (the [id] layout's gate), so
 * every mutating action below must refuse a read-only operator itself — the API enforces the same
 * permission, but the action must fail honestly rather than surface whatever the API's 403 says.
 * Returns the `fail` to hand back, or null when the caller may proceed.
 */
const writeRefusal = (locals: { permissions: string[]; role: string | null }, action: string) =>
	can(locals.permissions, locals.role, 'tenants:write')
		? null
		: fail(403, { action, error: 'You need the tenants:write permission to manage this firm.' });

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The firm's uid — what `/admin/tenants/{tenant_uid}` takes. It is the route's own parameter,
	// so nothing is resolved to get it.
	const tenantUid = params.uid;
	const headers = authHeaders(locals.accessToken);
	const [rolesRes, permsRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${tenantUid}/roles`, { headers }),
		fetch(`${apiUrl()}/admin/permissions`, { headers })
	]);
	const roles: OrgRole[] = rolesRes.ok ? await rolesRes.json() : [];
	const permissions: Permission[] = permsRes.ok ? await permsRes.json() : [];
	return { roles, permissions };
};

export const actions: Actions = {
	createRole: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'createRole');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const name = String(data.get('name') ?? '').trim();
		const slug = String(data.get('slug') ?? '').trim();
		const portal = String(data.get('portal') ?? '');
		if (!name || !slug)
			return fail(400, { action: 'createRole', error: 'Name and slug are required.' });
		if (portal !== 'client' && portal !== 'platform')
			return fail(400, { action: 'createRole', error: 'A portal is required.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/roles`, {
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

	// Pull in roles that exist on the firm's WorkOS orgs but never made it into the mirror — the
	// documented fix for a role that then 422s "already in use" for ever. The endpoint existed but
	// nothing called it, so the escape hatch was curl-only.
	syncRoles: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'syncRoles');
		if (refused) return refused;
		const tenantUid = params.uid;
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/roles/sync`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'syncRoles', error: await detail(res) });
		const body = await res.json();
		return { action: 'syncRoles', ok: true, synced: (body?.synced ?? 0) as number };
	},

	setRolePermissions: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'setRolePermissions');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const slug = String(data.get('slug') ?? '');
		if (!slug) return fail(400, { action: 'setRolePermissions', error: 'A role is required.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/roles/${slug}/permissions`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ permissions: data.getAll('permissions').map(String) })
		});
		if (!res.ok)
			return fail(res.status, { action: 'setRolePermissions', error: await detail(res) });
		return { action: 'setRolePermissions', ok: true, slug };
	}
};
