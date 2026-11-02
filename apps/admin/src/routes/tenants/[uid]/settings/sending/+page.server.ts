import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { DnsRecord } from '$lib/tenants/sender-records';
import type { Actions, PageServerLoad } from './$types';

export type { DnsRecord };

/** The firm's sending domain. `active` is the only state mail will actually use. */
export type SenderDomain = {
	uid: string;
	domain: string;
	region: string;
	status: string;
	sending_verified: boolean;
	active: boolean;
	from_address: string | null;
	verified_at: string | null;
	records: DnsRecord[];
};

/**
 * Server-side write guard: the page is reachable with tenants:read (the [uid] layout's gate), so
 * every mutating action below must refuse a read-only operator itself — the API enforces the same
 * permission, but the action must fail honestly rather than surface whatever the API's 403 says.
 */
const writeRefusal = (locals: { permissions: string[]; role: string | null }, action: string) =>
	can(locals.permissions, locals.role, 'tenants:write')
		? null
		: fail(403, { action, error: 'You need the tenants:write permission to manage this firm.' });

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/sender-domain`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return { domain: null as SenderDomain | null, error: await detail(res) };
	return { domain: (await res.json()) as SenderDomain | null, error: null as string | null };
};

async function send(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	action: string,
	url: string,
	method: string,
	body?: unknown
) {
	const res = await fetch(url, {
		method,
		headers: body === undefined ? authHeaders(accessToken) : jsonHeaders(accessToken),
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (!res.ok) return fail(res.status, { action, error: await detail(res) });
	return { action, ok: true };
}

export const actions: Actions = {
	register: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'register');
		if (refused) return refused;
		const data = await request.formData();
		return send(
			fetch,
			locals.accessToken,
			'register',
			`${apiUrl()}/admin/tenants/${params.uid}/sender-domain`,
			'POST',
			{ domain: String(data.get('domain') ?? '').trim() }
		);
	},

	setAddress: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'setAddress');
		if (refused) return refused;
		const data = await request.formData();
		return send(
			fetch,
			locals.accessToken,
			'setAddress',
			`${apiUrl()}/admin/tenants/${params.uid}/sender-domain/from`,
			'PUT',
			{ from_address: String(data.get('from_address') ?? '').trim() }
		);
	},

	verify: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'verify');
		if (refused) return refused;
		return send(
			fetch,
			locals.accessToken,
			'verify',
			`${apiUrl()}/admin/tenants/${params.uid}/sender-domain/verify`,
			'POST'
		);
	},

	refresh: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'refresh');
		if (refused) return refused;
		return send(
			fetch,
			locals.accessToken,
			'refresh',
			`${apiUrl()}/admin/tenants/${params.uid}/sender-domain/refresh`,
			'POST'
		);
	},

	remove: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'remove');
		if (refused) return refused;
		return send(
			fetch,
			locals.accessToken,
			'remove',
			`${apiUrl()}/admin/tenants/${params.uid}/sender-domain`,
			'DELETE'
		);
	},

	test: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'test');
		if (refused) return refused;
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/sender-domain/test`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'test', error: await detail(res) });
		const body = (await res.json()) as { status?: string; to?: string; detail?: string | null };
		return {
			action: 'test',
			ok: true,
			status: body.status ?? 'failed',
			to: body.to ?? '',
			detail: body.detail ?? null
		};
	}
};
