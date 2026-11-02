import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { Actions, PageServerLoad } from './$types';

export type StorageInfo = {
	endpoint: string | null;
	bucket: string;
	region: string | null;
	read_prefixes: string[];
	shared: boolean;
	credentials_supported: boolean;
	credential: {
		id: number;
		access_key_id: string;
		read_prefixes: string[];
		shared: boolean;
		created_at: string;
	} | null;
};

const STORAGE_PERMS = [
	'storage:read',
	'storage:read_all',
	'storage:shared',
	'storage:credentials_own',
	'storage:credentials'
] as const;

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const authorised = STORAGE_PERMS.some((p) => can(locals.permissions, locals.role, p));
	const canIssue =
		can(locals.permissions, locals.role, 'storage:credentials_own') ||
		can(locals.permissions, locals.role, 'storage:credentials');
	if (!authorised) return { authorised: false, canIssue: false, storage: null, error: null };

	const res = await fetch(`${apiUrl()}/storage`, { headers: authHeaders(locals.accessToken) });
	if (res.status === 409) {
		// Storage not provisioned for this firm yet — show the page, explain the state.
		return { authorised: true, canIssue, storage: null, error: await detail(res) };
	}
	if (!res.ok) return { authorised: true, canIssue, storage: null, error: await detail(res) };
	return { authorised: true, canIssue, storage: (await res.json()) as StorageInfo, error: null };
};

export const actions: Actions = {
	issue: async ({ locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const res = await fetch(`${apiUrl()}/storage/credentials`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		// Contains the secret — shown once on this response, never retrievable again.
		return { issued: await res.json() };
	},
	revoke: async ({ locals, fetch, request }) => {
		if (!locals.user) redirect(303, '/login');
		const data = await request.formData();
		const uid = String(data.get('uid') ?? '');
		const res = await fetch(`${apiUrl()}/storage/credentials/${encodeURIComponent(uid)}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { revoked: true };
	}
};
