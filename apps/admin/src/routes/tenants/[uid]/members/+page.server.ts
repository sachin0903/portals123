import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type Member, type OrgRole } from '$lib/api';
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
	const [membersRes, rolesRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members`, { headers }),
		fetch(`${apiUrl()}/admin/tenants/${tenantUid}/roles`, { headers })
	]);
	const members: Member[] = membersRes.ok ? await membersRes.json() : [];
	const roles: OrgRole[] = rolesRes.ok ? await rolesRes.json() : [];
	// A failed fetch used to render as "no members" / "no roles" — indistinguishable from the real
	// empty state. Surface it instead.
	const apiError = !membersRes.ok
		? await detail(membersRes)
		: !rolesRes.ok
			? await detail(rolesRes)
			: null;
	// This firm's own roles are the assignable set (no global templates).
	// The client + platform portals are managed here; operations lives elsewhere.
	const roleOptions = roles.map((r) => ({ slug: r.slug, name: r.name, portal: r.portal }));
	return { members, roleOptions, apiError };
};

export const actions: Actions = {
	sync: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'sync');
		if (refused) return refused;
		const tenantUid = params.uid;
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/sync`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'sync', error: await detail(res) });
		// `imported` and `withoutPortal` alongside `members`: the reconcile deliberately never
		// invents a portal row (which portals a person may use is this app's call, not WorkOS's),
		// so it can import a firm's whole staff list and grant none of them anything -- correct,
		// and previously reported as a bare "Synced 33 member(s)", which reads as a no-op.
		const body = await res.json();
		return {
			action: 'sync',
			ok: true,
			members: (body?.members ?? 0) as number,
			imported: (body?.imported ?? 0) as number,
			withoutPortal: (body?.without_portal ?? 0) as number
		};
	},

	addMember: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'addMember');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const email = String(data.get('email') ?? '').trim();
		const roleSlug = String(data.get('role_slug') ?? '');
		if (!email || !roleSlug)
			return fail(400, { action: 'addMember', error: 'A valid email and role are required.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				email,
				role_slug: roleSlug,
				first_name: String(data.get('first_name') ?? '').trim() || null,
				last_name: String(data.get('last_name') ?? '').trim() || null
			})
		});
		if (!res.ok) return fail(res.status, { action: 'addMember', error: await detail(res) });
		const body = await res.json();
		// The set-password link is NOT returned here — it is a live credential. `emailDelivered`
		// is what lets the toast stop claiming "sent" when Resend isn't configured; the operator
		// then uses "Copy invite link" on the row.
		return {
			action: 'addMember',
			ok: true,
			email,
			emailDelivered: Boolean(body?.email_delivered)
		};
	},

	// Grant (or reactivate) a portal for a person, assigning a role within it.
	grantPortal: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'grantPortal');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const portal = String(data.get('portal') ?? '');
		const roleSlug = String(data.get('role_slug') ?? '');
		if (!userId || !portal || !roleSlug)
			return fail(400, {
				action: 'grantPortal',
				error: 'A user, portal and role are required.'
			});
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/portals/${portal}`,
			{
				method: 'PUT',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ role_slug: roleSlug })
			}
		);
		if (!res.ok)
			return fail(res.status, { action: 'grantPortal', userId, portal, error: await detail(res) });
		return { action: 'grantPortal', ok: true, userId, portal };
	},

	// Revoke a portal from a person.
	revokePortal: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'revokePortal');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const portal = String(data.get('portal') ?? '');
		if (!userId || !portal)
			return fail(400, { action: 'revokePortal', error: 'A user and portal are required.' });
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/portals/${portal}`,
			{ method: 'DELETE', headers: authHeaders(locals.accessToken) }
		);
		if (!res.ok)
			return fail(res.status, { action: 'revokePortal', userId, portal, error: await detail(res) });
		return { action: 'revokePortal', ok: true, userId, portal };
	},

	// Change the role a person holds within a portal they already have.
	setRole: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'setRole');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const portal = String(data.get('portal') ?? '');
		const roleSlug = String(data.get('role_slug') ?? '');
		if (!userId || !portal || !roleSlug)
			return fail(400, { action: 'setRole', error: 'A user, portal and role are required.' });
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/portals/${portal}`,
			{
				method: 'PATCH',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ role_slug: roleSlug })
			}
		);
		if (!res.ok)
			return fail(res.status, { action: 'setRole', userId, portal, error: await detail(res) });
		return { action: 'setRole', ok: true, userId, portal };
	},

	deactivate: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'deactivate');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'deactivate', error: 'A user is required.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/deactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'deactivate', error: await detail(res) });
		return { action: 'deactivate', ok: true, userId };
	},

	reactivate: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'reactivate');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'reactivate', error: 'A user is required.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/reactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'reactivate', error: await detail(res) });
		const body = await res.json();
		// What came back is per-portal now (restored from the pre-suspension snapshot), so the toast
		// can name a portal that stayed revoked instead of implying a blanket restore.
		return {
			action: 'reactivate',
			ok: true,
			userId,
			portalsRestored: (body?.portals_restored ?? []) as string[],
			portalsLeftRevoked: (body?.portals_left_revoked ?? []) as string[],
			snapshotFound: Boolean(body?.snapshot_found)
		};
	},

	// Re-send the onboarding email. `portal` targets the link at the portal the person is pending
	// on, rather than whichever membership happened to sort first.
	resendInvite: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'resendInvite');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const portal = String(data.get('portal') ?? '');
		if (!userId) return fail(400, { action: 'resendInvite', error: 'A user is required.' });
		const query = portal ? `?portal=${encodeURIComponent(portal)}` : '';
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/resend-invite${query}`,
			{ method: 'POST', headers: authHeaders(locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { action: 'resendInvite', error: await detail(res) });
		const body = await res.json();
		return {
			action: 'resendInvite',
			ok: true,
			userId,
			emailDelivered: Boolean(body?.email_delivered)
		};
	},

	// Deliberately reveal a fresh set-password link for copying — the escape hatch when email
	// can't be delivered. Its own action (and its own audited endpoint) so a live credential is
	// never returned by the ordinary invite/resend responses.
	invitationLink: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'invitationLink');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const portal = String(data.get('portal') ?? '');
		if (!userId) return fail(400, { action: 'invitationLink', error: 'A user is required.' });
		const query = portal ? `?portal=${encodeURIComponent(portal)}` : '';
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/invitation-link${query}`,
			{ method: 'POST', headers: authHeaders(locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { action: 'invitationLink', error: await detail(res) });
		const body = await res.json();
		return {
			action: 'invitationLink',
			ok: true,
			userId,
			invitationUrl: (body?.invitation_url ?? null) as string | null
		};
	},

	// Clear enrolled authenticators so a person who lost their device can re-enrol.
	resetMfa: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'resetMfa');
		if (refused) return refused;
		const tenantUid = params.uid;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'resetMfa', error: 'A user is required.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members/${userId}/reset-mfa`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'resetMfa', error: await detail(res) });
		const body = await res.json();
		return {
			action: 'resetMfa',
			ok: true,
			userId,
			factorsDeleted: (body?.factors_deleted ?? 0) as number
		};
	}
};
