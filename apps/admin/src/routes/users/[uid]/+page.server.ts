import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type OrgRole, type Portal } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

export type User = {
	/** `auth.users.id` — a uuid, and the handle the member endpoints are typed on. Internal: it
	 *  never appears in a URL. */
	id: string;
	/** `auth.users.uid` — the login's public handle, which is what this route is addressed by. */
	uid: string;
	email: string;
	name: string | null;
	enabled: boolean;
	mobile: string | null;
	workos_user_id: string | null;
	last_sign_in: string | null;
};

/**
 * One membership row = one (firm, portal) pair. `portal` is what the API has always returned and
 * what the role endpoints are keyed on; omitting it here is what made this page post to a
 * non-existent route and collide its `each` keys for anyone holding two portals in one firm.
 */
export type Membership = {
	tenant_id: number;
	/** The firm's uid — what the admin tenant routes are addressed by. The numeric `tenant_id`
	 *  stays because the per-firm role fetches take the API's own integer contract. */
	tenant_uid: string;
	tenant_name: string;
	tenant_alias: string;
	portal: Portal;
	role_slug: string | null;
	status: string;
};

/** The roles a firm actually defines, per portal — there are no global role templates. */
export type TenantRoles = Record<number, { slug: string; name: string; portal: Portal | null }[]>;

const writeRefusal = (locals: { permissions: string[]; role: string | null }, action: string) =>
	can(locals.permissions, locals.role, 'tenants:write')
		? null
		: fail(403, {
				action,
				error: 'You need the tenants:write permission to manage this identity.'
			});

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'tenants:read')) error(403, 'Not authorised');
	const headers = authHeaders(locals.accessToken);
	// The URL names the login by its uid; `auth.users.id` is the database's own key. `by-uid`
	// answers with the whole record, so addressing by uid costs the same single call.
	const res = await fetch(`${apiUrl()}/admin/users/by-uid/${params.uid}`, { headers });
	if (res.status === 403) error(403, 'System role required.');
	if (res.status === 404) error(404, 'User not found.');
	if (!res.ok) error(res.status, await detail(res));
	const data: { user: User; memberships: Membership[] } = await res.json();

	// The assignable roles come from each firm's own role set (one fetch per distinct firm), so the
	// picker offers roles that exist and the server validates against the same list.
	const tenantUids = [...new Set(data.memberships.map((m) => m.tenant_uid))];
	const roleLists = await Promise.all(
		tenantUids.map(async (uid) => {
			const r = await fetch(`${apiUrl()}/admin/tenants/${uid}/roles`, { headers });
			const roles: OrgRole[] = r.ok ? await r.json() : [];
			return [
				uid,
				roles.map((x) => ({ slug: x.slug, name: x.name, portal: x.portal })),
				r.ok
			] as const;
		})
	);
	const roles: TenantRoles = Object.fromEntries(roleLists.map(([id, list]) => [id, list]));
	// Firms whose roles we couldn't read (e.g. the operations tenant needs `operations:read`). An
	// empty list there means "not allowed to see", not "this firm has no roles" — the page has to
	// say which.
	const roleErrors = roleLists.filter(([, , ok]) => !ok).map(([id]) => id);

	return {
		...data,
		roles,
		roleErrors,
		canManage: can(locals.permissions, locals.role, 'tenants:write')
	};
};

/**
 * The uuid behind a login's uid.
 *
 * The member endpoints (`/admin/tenants/{id}/members/{user_id}/…`) are typed on `auth.users.id`,
 * and a form action cannot read its page's data — so it resolves first. A page LOAD does not need
 * this: it fetches by uid and has the uuid in hand.
 */
async function userIdFor(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	uid: string
): Promise<string> {
	const res = await fetch(`${apiUrl()}/admin/users/by-uid/${uid}`, {
		headers: authHeaders(accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : res.status, await detail(res));
	const body = (await res.json()) as { user: { id: string } };
	return body.user.id;
}

/** The roles a firm defines for one portal — the server-side half of the picker's validation. */
async function portalRoleSlugs(
	tenantUid: string,
	portal: string,
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<string[]> {
	const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/roles`, {
		headers: authHeaders(accessToken)
	});
	if (!res.ok) return [];
	const roles: OrgRole[] = await res.json();
	return roles.filter((r) => r.portal === portal).map((r) => r.slug);
}

const PORTALS = ['client', 'platform', 'operations'];

export const actions: Actions = {
	// Change the role a person holds in ONE portal of ONE firm. Roles are per-tenant with
	// arbitrary slugs, so the submitted slug is validated against that firm's own roles.
	changeRole: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'changeRole');
		if (refused) return refused;
		const data = await request.formData();
		const tenantUid = String(data.get('tenant_uid') ?? '');
		const portal = String(data.get('portal') ?? '');
		const roleSlug = String(data.get('role_slug') ?? '');
		if (!tenantUid || !PORTALS.includes(portal) || !roleSlug)
			return fail(400, { error: 'A firm, portal and role are required.' });
		const allowed = await portalRoleSlugs(tenantUid, portal, locals.accessToken, fetch);
		if (!allowed.includes(roleSlug))
			return fail(400, { error: `'${roleSlug}' is not a ${portal} role in this firm.` });
		const userId = await userIdFor(fetch, locals.accessToken, params.uid);
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/portals/${portal}`,
			{
				method: 'PATCH',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ role_slug: roleSlug })
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	// Take away one portal, leaving any other access this person holds in the firm intact.
	revokePortal: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'revokePortal');
		if (refused) return refused;
		const data = await request.formData();
		const tenantUid = String(data.get('tenant_uid') ?? '');
		const portal = String(data.get('portal') ?? '');
		if (!tenantUid || !PORTALS.includes(portal))
			return fail(400, { error: 'A firm and portal are required.' });
		const userId = await userIdFor(fetch, locals.accessToken, params.uid);
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/portals/${portal}`,
			{ method: 'DELETE', headers: authHeaders(locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	// Suspend the person across the WHOLE firm (every portal) — named for what it does.
	suspendInFirm: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'suspendInFirm');
		if (refused) return refused;
		const data = await request.formData();
		const tenantUid = String(data.get('tenant_uid') ?? '');
		if (!tenantUid) return fail(400, { error: 'A firm is required.' });
		const userId = await userIdFor(fetch, locals.accessToken, params.uid);
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/deactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	restoreInFirm: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'restoreInFirm');
		if (refused) return refused;
		const data = await request.formData();
		const tenantUid = String(data.get('tenant_uid') ?? '');
		if (!tenantUid) return fail(400, { error: 'A firm is required.' });
		const userId = await userIdFor(fetch, locals.accessToken, params.uid);
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/reactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	// Clear enrolled MFA factors — the recovery path for a lost authenticator.
	resetMfa: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'resetMfa');
		if (refused) return refused;
		const data = await request.formData();
		const tenantUid = String(data.get('tenant_uid') ?? '');
		if (!tenantUid) return fail(400, { error: 'A firm is required.' });
		const userId = await userIdFor(fetch, locals.accessToken, params.uid);
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/reset-mfa`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const body = await res.json();
		return { ok: true, factorsDeleted: (body?.factors_deleted ?? 0) as number };
	}
};
