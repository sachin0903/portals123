import { error, fail, redirect } from '@sveltejs/kit';
import { fillCurrentBrowser, readSignInClient } from '@graphene/auth';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { AuthProfileData } from '@graphene/ui';
import type { RequestEvent } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';

async function operationsTenant(accessToken: string | null, fetch: typeof globalThis.fetch) {
	const res = await fetch(`${apiUrl()}/admin/operations`, { headers: authHeaders(accessToken) });
	if (!res.ok) error(res.status, await detail(res));
	return (await res.json()).tenant_uid as string;
}

async function profileFor(event: Pick<RequestEvent, 'fetch' | 'locals' | 'params'>) {
	const tenantUid = await operationsTenant(event.locals.accessToken, event.fetch);
	const res = await event.fetch(
		`${apiUrl()}/admin/tenants/${tenantUid}/members/by-uid/${event.params.uid}`,
		{ headers: authHeaders(event.locals.accessToken) }
	);
	if (res.status === 404) error(404, 'Operations user not found');
	if (!res.ok) error(res.status, await detail(res));
	return { tenantUid, profile: (await res.json()) as AuthProfileData };
}

export const load: PageServerLoad = async (event) => {
	if (!event.locals.user) redirect(303, '/login');
	if (!can(event.locals.permissions, event.locals.role, 'operations:read'))
		error(403, 'Not authorised');
	const loaded = await profileFor(event);
	if (loaded.profile.security?.sessions) {
		loaded.profile.security.sessions = fillCurrentBrowser(
			loaded.profile.security.sessions,
			readSignInClient(event.request, event.getClientAddress)
		);
	}
	return {
		...loaded,
		canManage: can(event.locals.permissions, event.locals.role, 'operations:write')
	};
};

async function mutate(event: RequestEvent, action: string) {
	if (!event.locals.user) redirect(303, '/login');
	if (!can(event.locals.permissions, event.locals.role, 'operations:write'))
		return fail(403, { error: 'You do not have permission to manage operations staff.' });
	const { tenantUid, profile } = await profileFor(event);
	const paths: Record<string, string> = {
		passwordReset: `${profile.user.id}/password-reset?portal=operations`,
		resetMfa: `${profile.user.id}/reset-mfa`,
		deactivate: `${profile.user.id}/deactivate`,
		reactivate: `${profile.user.id}/reactivate`
	};
	const res = await event.fetch(`${apiUrl()}/admin/tenants/${tenantUid}/members/${paths[action]}`, {
		method: 'POST',
		headers: authHeaders(event.locals.accessToken)
	});
	if (!res.ok) return fail(res.status, { action, error: await detail(res) });
	const body = await res.json().catch(() => ({}));
	return {
		action,
		ok: true,
		emailDelivered: body?.email_delivered as boolean | undefined,
		factorsDeleted: body?.factors_deleted as number | undefined
	};
}

export const actions: Actions = {
	passwordReset: (event) => mutate(event, 'passwordReset'),
	resetMfa: (event) => mutate(event, 'resetMfa'),
	deactivate: (event) => mutate(event, 'deactivate'),
	reactivate: (event) => mutate(event, 'reactivate'),
	revokeSession: async (event) => {
		if (!event.locals.user) redirect(303, '/login');
		if (!can(event.locals.permissions, event.locals.role, 'operations:write'))
			return fail(403, { error: 'You do not have permission to manage operations staff.' });
		const data = await event.request.formData();
		const sessionId = String(data.get('session_id') ?? '').trim();
		if (!sessionId) return fail(400, { error: 'A session is required.' });
		const { tenantUid, profile } = await profileFor(event);
		const res = await event.fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/members/${profile.user.id}/sessions/${encodeURIComponent(sessionId)}`,
			{ method: 'POST', headers: authHeaders(event.locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
