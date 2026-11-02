import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type OrgRole, type Permission } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { Actions, PageServerLoad } from './$types';

/** The editor's two lists, in one streamed object — or why there are none. */
export type RoleCatalogue = {
	roles: OrgRole[];
	permissions: Permission[];
	/** The API refused (403) a caller the portal's own check let through — the same banner as
	 *  having no `roles:read` at all. */
	forbidden: boolean;
	loadError: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'roles:read')) {
		return { authorised: false, canManage: false, catalogue: null };
	}

	// The permission answers are SYNCHRONOUS and the catalogue is STREAMED: the heading renders the
	// moment the page is navigated to and the editor lands behind it, rather than the first byte
	// waiting on two API reads (`clients/+page.server.ts` is the pattern). `redirect` stays out
	// here — it has to be thrown before the response starts.
	return {
		authorised: true,
		canManage: can(locals.permissions, locals.role, 'roles:write'),
		catalogue: loadCatalogue(locals.accessToken, fetch)
	};
};

async function loadCatalogue(
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<RoleCatalogue> {
	const headers = authHeaders(accessToken);
	// Caught whole: a streamed promise that rejects is a broken page rather than a banner.
	try {
		const [rolesRes, permsRes] = await Promise.all([
			fetch(`${apiUrl()}/firm/roles`, { headers }),
			fetch(`${apiUrl()}/firm/permissions`, { headers })
		]);
		// The API also enforces roles:read (403).
		if (rolesRes.status === 403) {
			return { roles: [], permissions: [], forbidden: true, loadError: false };
		}
		const roles: OrgRole[] = rolesRes.ok ? await rolesRes.json() : [];
		const permissions: Permission[] = permsRes.ok ? await permsRes.json() : [];
		return {
			roles,
			permissions,
			forbidden: false,
			loadError: !rolesRes.ok || !permsRes.ok
		};
	} catch {
		return { roles: [], permissions: [], forbidden: false, loadError: true };
	}
}

/** Reject unless the caller can manage roles; keeps every action gated in one place. */
function requireManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'roles:write')) redirect(303, '/team/roles');
}

export const actions: Actions = {
	createRole: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const name = String(data.get('name') ?? '').trim();
		const slug = String(data.get('slug') ?? '').trim();
		const portal = String(data.get('portal') ?? '');
		if (!name || !slug)
			return fail(400, { action: 'createRole', error: 'Name and slug are required.' });
		if (portal !== 'client' && portal !== 'platform')
			return fail(400, { action: 'createRole', error: 'A portal is required.' });
		const res = await fetch(`${apiUrl()}/firm/roles`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				name,
				slug,
				portal,
				description: String(data.get('description') ?? '').trim() || null,
				permissions: data.getAll('permissions').map(String)
			})
		});
		if (!res.ok) return fail(res.status, { action: 'createRole', error: await detail(res) });
		return { action: 'createRole', ok: true };
	},

	setRolePermissions: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const slug = String(data.get('slug') ?? '');
		if (!slug) return fail(400, { action: 'setRolePermissions', error: 'A role is required.' });
		const res = await fetch(`${apiUrl()}/firm/roles/${slug}/permissions`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ permissions: data.getAll('permissions').map(String) })
		});
		if (!res.ok)
			return fail(res.status, { action: 'setRolePermissions', error: await detail(res) });
		return { action: 'setRolePermissions', ok: true, slug };
	}
};
