import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { fillCurrentBrowser, type SignInClient } from '@graphene/auth';
import type { AuthProfileData } from '@graphene/ui';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import type { RequestEvent } from '@sveltejs/kit';

type MemberKind = 'team' | 'profiles';
type ActionName = 'passwordReset' | 'resetMfa' | 'deactivate' | 'reactivate' | 'revokeInvitation';

export async function loadMemberProfile(
	locals: App.Locals,
	fetch: typeof globalThis.fetch,
	uid: string,
	kind: MemberKind,
	client?: SignInClient
) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'members:read')) error(403, 'Not authorised');
	const res = await fetch(`${apiUrl()}/firm/members/by-uid/${uid}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (res.status === 404)
		error(404, kind === 'team' ? 'Team member not found' : 'Profile not found');
	if (!res.ok) error(res.status, await detail(res));
	const profile = (await res.json()) as AuthProfileData;
	if (profile.security?.sessions) {
		profile.security.sessions = fillCurrentBrowser(profile.security.sessions, client);
	}
	const expectedType = kind === 'team' ? 'staff' : 'client';
	if (!profile.user.types.includes(expectedType)) error(404, 'Auth profile not found');
	return {
		profile,
		canManage: can(locals.permissions, locals.role, 'members:write'),
		kind
	};
}

async function memberId(
	event: Pick<RequestEvent, 'fetch' | 'locals'>,
	uid: string
): Promise<string> {
	const res = await event.fetch(`${apiUrl()}/firm/members/by-uid/${uid}`, {
		headers: authHeaders(event.locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return ((await res.json()) as AuthProfileData).user.id;
}

export async function runMemberAction(
	event: RequestEvent,
	action: ActionName,
	portal: 'client' | 'platform'
) {
	if (!event.locals.user) redirect(303, '/login');
	if (!can(event.locals.permissions, event.locals.role, 'members:write'))
		return fail(403, { error: 'You do not have permission to manage this identity.' });
	const uid = event.params.uid;
	if (!uid) return fail(400, { error: 'An auth profile is required.' });
	const userId = await memberId(event, uid);
	const paths: Record<ActionName, string> = {
		passwordReset: `${userId}/password-reset?portal=${portal}`,
		resetMfa: `${userId}/reset-mfa`,
		deactivate: `${userId}/deactivate`,
		reactivate: `${userId}/reactivate`,
		revokeInvitation: `${userId}/invitation`
	};
	const res = await event.fetch(`${apiUrl()}/firm/members/${paths[action]}`, {
		method: action === 'revokeInvitation' ? 'DELETE' : 'POST',
		headers: authHeaders(event.locals.accessToken)
	});
	if (!res.ok) return fail(res.status, { action, error: await detail(res) });
	const body = await res.json().catch(() => ({}));
	return {
		action,
		ok: true,
		emailDelivered: body?.email_delivered as boolean | undefined,
		factorsDeleted: body?.factors_deleted as number | undefined,
		sessionsRevoked: body?.sessions_revoked as number | undefined
	};
}

export async function revokeMemberSession(event: RequestEvent) {
	if (!event.locals.user) redirect(303, '/login');
	if (!can(event.locals.permissions, event.locals.role, 'members:write'))
		return fail(403, { error: 'You do not have permission to manage this identity.' });
	const uid = event.params.uid;
	if (!uid) return fail(400, { error: 'An auth profile is required.' });
	const data = await event.request.formData();
	const sessionId = String(data.get('session_id') ?? '').trim();
	if (!sessionId) return fail(400, { error: 'A session is required.' });
	const userId = await memberId(event, uid);
	const res = await event.fetch(
		`${apiUrl()}/firm/members/${userId}/sessions/${encodeURIComponent(sessionId)}`,
		{ method: 'POST', headers: authHeaders(event.locals.accessToken) }
	);
	if (!res.ok) return fail(res.status, { action: 'revokeSession', error: await detail(res) });
	return { action: 'revokeSession', ok: true };
}

/** Name, job title, and whether they may advise. The listing used to own this as a drawer;
 *  the person page is the editor now. */
export async function updateMemberProfile(event: RequestEvent) {
	if (!event.locals.user) redirect(303, '/login');
	if (!can(event.locals.permissions, event.locals.role, 'members:write'))
		return fail(403, { error: 'You do not have permission to manage this identity.' });
	const uid = event.params.uid;
	if (!uid) return fail(400, { error: 'An auth profile is required.' });
	const userId = await memberId(event, uid);
	const data = await event.request.formData();
	const body: Record<string, unknown> = {};
	for (const field of ['first_name', 'last_name', 'job_title']) {
		if (data.has(field)) body[field] = String(data.get(field) ?? '').trim() || null;
	}
	// Adviser is its own section. A name save must not post `is_adviser`, or turning the
	// switch off would be a side effect of editing a first name.
	const res = await event.fetch(`${apiUrl()}/firm/members/${userId}`, {
		method: 'PUT',
		headers: jsonHeaders(event.locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return fail(res.status, { action: 'updateUser', error: await detail(res) });
	return { action: 'updateUser', ok: true, userId };
}

/** The adviser switch, FCA reference, and the custodian organisation they open clients under.
 *  Separate from the name save so one cannot clear the other. An absent field is left untouched;
 *  a present one is written, and a blank box clears it. The switch form does not carry the
 *  organisation fields, so turning adviser on or off cannot wipe them. */
export async function updateAdviserProfile(event: RequestEvent) {
	if (!event.locals.user) redirect(303, '/login');
	if (!can(event.locals.permissions, event.locals.role, 'members:write'))
		return fail(403, { error: 'You do not have permission to manage this identity.' });
	const uid = event.params.uid;
	if (!uid) return fail(400, { error: 'An auth profile is required.' });
	const userId = await memberId(event, uid);
	const data = await event.request.formData();
	const body: Record<string, unknown> = {};
	if (data.has('is_adviser')) body.is_adviser = data.get('is_adviser') === 'on';
	if (data.has('fca_reference'))
		body.fca_reference = String(data.get('fca_reference') ?? '').trim() || null;
	for (const field of ['seccl_node', 'sei_dsr'] as const) {
		if (data.has(field)) body[field] = String(data.get(field) ?? '').trim() || null;
	}
	if (Object.keys(body).length === 0)
		return fail(400, { action: 'setAdviser', error: 'Nothing to update.' });
	const res = await event.fetch(`${apiUrl()}/firm/members/${userId}`, {
		method: 'PUT',
		headers: jsonHeaders(event.locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return fail(res.status, { action: 'setAdviser', error: await detail(res) });
	return { action: 'setAdviser', ok: true, userId };
}

/**
 * Record (or clear) which id a custodian knows this person by — the MANUAL half of the
 * mapping, for the people `auth.merge_provider_user` declined to guess at.
 *
 * Its own action rather than part of `updateUser`, because it is its own endpoint: the same
 * `provisioning.set_provider_ref` the operations route calls, so what a mapping IS cannot
 * differ by who set it. A BLANK id clears the key rather than storing an empty string.
 */
export async function setMemberProviderRef(event: RequestEvent) {
	if (!event.locals.user) redirect(303, '/login');
	if (!can(event.locals.permissions, event.locals.role, 'members:write'))
		return fail(403, { error: 'You do not have permission to manage this identity.' });
	const uid = event.params.uid;
	if (!uid) return fail(400, { error: 'An auth profile is required.' });
	const data = await event.request.formData();
	const provider = String(data.get('provider') ?? '');
	if (!provider)
		return fail(400, { action: 'setProviderRef', error: 'A user and provider are required.' });
	const userId = await memberId(event, uid);
	const res = await event.fetch(`${apiUrl()}/firm/members/${userId}/provider-ref/${provider}`, {
		method: 'PUT',
		headers: jsonHeaders(event.locals.accessToken),
		body: JSON.stringify({ external_id: String(data.get('external_id') ?? '').trim() })
	});
	if (!res.ok) return fail(res.status, { action: 'setProviderRef', error: await detail(res) });
	return { action: 'setProviderRef', ok: true, userId, provider };
}
