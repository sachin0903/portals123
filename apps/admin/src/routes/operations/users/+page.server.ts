import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type Member, type OrgRole } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** Ops staff hold a single operations-portal role — this page reads the flattened shape. */
type OpsMember = {
	user_id: string;
	user_uid: string;
	email: string;
	name: string | null;
	enabled: boolean;
	role_slug: string | null;
	status: string;
};

/** Flatten a portal-aware member down to its operations-portal role for this page. */
function toOpsMember(m: Member): OpsMember {
	const ops = m.portals.find((p) => p.portal === 'operations');
	return {
		user_id: m.user_id,
		user_uid: m.user_uid,
		email: m.email,
		name: m.name,
		enabled: m.enabled,
		role_slug: ops?.role_slug ?? null,
		status: ops?.status ?? (m.enabled ? 'active' : 'inactive')
	};
}

/** The tenant that owns the core project (Graphene) — where ops staff/roles live. */
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
				error: 'You need the operations:write permission to manage operations staff.'
			});

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'operations:read')) error(403, 'Not authorised');
	const canManage = can(locals.permissions, locals.role, 'operations:write');
	const headers = authHeaders(locals.accessToken);
	const uid = await opsTenantUid(locals.accessToken, fetch);
	const [membersRes, rolesRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/tenants/${uid}/members`, { headers }),
		fetch(`${apiUrl()}/admin/tenants/${uid}/roles`, { headers })
	]);
	const raw: Member[] = membersRes.ok ? await membersRes.json() : [];
	// Only show people who actually hold the operations portal, flattened for this page.
	const members: OpsMember[] = raw
		.filter((m) => m.portals.some((p) => p.portal === 'operations'))
		.map(toOpsMember);
	const roles: OrgRole[] = rolesRes.ok ? await rolesRes.json() : [];
	// Ops staff are assigned the operations-portal roles only.
	const roleOptions = roles
		.filter((r) => r.portal === 'operations')
		.map((r) => ({ slug: r.slug, name: r.name }));
	return { members, roleOptions, canManage };
};

export const actions: Actions = {
	addMember: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'addMember');
		if (refused) return refused;
		const data = await request.formData();
		const email = String(data.get('email') ?? '').trim();
		const roleSlug = String(data.get('role_slug') ?? '');
		if (!email || !roleSlug)
			return fail(400, { action: 'addMember', error: 'A valid email and role are required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/members`, {
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
		// The set-password link is a live credential and is not returned here — `invitationLink`
		// below reveals one deliberately. `emailDelivered` keeps the toast honest.
		return {
			action: 'addMember',
			ok: true,
			email,
			emailDelivered: Boolean(body?.email_delivered)
		};
	},

	changeRole: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'changeRole');
		if (refused) return refused;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const roleSlug = String(data.get('role_slug') ?? '');
		if (!userId || !roleSlug)
			return fail(400, { action: 'changeRole', error: 'A user and a valid role are required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${uid}/members/${userId}/portals/operations`,
			{
				method: 'PATCH',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ role_slug: roleSlug })
			}
		);
		if (!res.ok) return fail(res.status, { action: 'changeRole', error: await detail(res) });
		return { action: 'changeRole', ok: true, userId };
	},

	deactivate: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'deactivate');
		if (refused) return refused;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'deactivate', error: 'A user is required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/members/${userId}/deactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'deactivate', error: await detail(res) });
		return { action: 'deactivate', ok: true, userId };
	},

	reactivate: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'reactivate');
		if (refused) return refused;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'reactivate', error: 'A user is required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/members/${userId}/reactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'reactivate', error: await detail(res) });
		return { action: 'reactivate', ok: true, userId };
	},

	// Ops invites had no recovery path at all: if the email didn't arrive there was nothing to
	// re-send and no link to copy. Both now exist, matching the firm members page.
	resendInvite: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'resendInvite');
		if (refused) return refused;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'resendInvite', error: 'A user is required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${uid}/members/${userId}/resend-invite?portal=operations`,
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

	invitationLink: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'invitationLink');
		if (refused) return refused;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'invitationLink', error: 'A user is required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${uid}/members/${userId}/invitation-link?portal=operations`,
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

	resetMfa: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'resetMfa');
		if (refused) return refused;
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'resetMfa', error: 'A user is required.' });
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/members/${userId}/reset-mfa`, {
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
	},

	sync: async ({ locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'sync');
		if (refused) return refused;
		const uid = await opsTenantUid(locals.accessToken, fetch);
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/sync`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'sync', error: await detail(res) });
		const body = await res.json();
		return { action: 'sync', ok: true, members: (body?.members ?? 0) as number };
	}
};
