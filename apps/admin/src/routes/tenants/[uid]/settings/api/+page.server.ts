import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** One partner-API key as the API lists it. The secret is never part of it: the create response
 *  carries that once, beside the key. */
export type ApiKey = {
	uid: string;
	name: string;
	/** The leading characters of the secret, so the firm can tell its keys apart. */
	key_prefix: string;
	scopes: string[];
	status: 'active' | 'revoked' | 'expired';
	created_at: string;
	last_used_at: string | null;
	expires_at: string | null;
	revoked_at: string | null;
};

/** The firm's check webhook. Every field is null while no URL is set. */
export type Webhook = {
	url: string | null;
	created_at: string | null;
	updated_at: string | null;
};

/** One queued or finished send of a check event to the firm's webhook. */
export type WebhookDelivery = {
	uid: string;
	event: 'check.updated';
	check_uid: string | null;
	kind: 'identity' | 'aml' | null;
	check_status: 'pending' | 'clear' | 'refer' | 'rejected' | null;
	status: 'pending' | 'sent' | 'failed';
	attempts: number;
	next_attempt_at: string;
	last_error: string | null;
	sent_at: string | null;
	created_at: string;
	updated_at: string;
};

/** How many deliveries the Recent deliveries card shows, newest first. */
const DELIVERY_LIMIT = 50;

/**
 * Server-side write guard: the page is reachable with tenants:read (the [uid] layout's gate), so
 * every mutating action below must refuse a read-only operator itself — the API enforces the same
 * permission, but the action must fail honestly rather than surface whatever the API's 403 says.
 * Returns the `fail` to hand back, or null when the caller may proceed.
 */
const writeRefusal = (locals: { permissions: string[]; role: string | null }, action: string) =>
	can(locals.permissions, locals.role, 'tenants:write')
		? null
		: fail(403, { action, error: 'You need the tenants:write permission to manage this firm.' });

/** One card's read: its body, or the message the card shows in its place. A failure here never
 *  fails the page — the other two cards still render. */
async function read<T>(
	fetch: typeof globalThis.fetch,
	url: string,
	accessToken: string | null
): Promise<{ body: T | null; error: string | null }> {
	try {
		const res = await fetch(url, { headers: authHeaders(accessToken) });
		if (!res.ok) return { body: null, error: await detail(res) };
		return { body: (await res.json()) as T, error: null };
	} catch {
		return { body: null, error: 'The service is unavailable.' };
	}
}

/** A `YYYY-MM-DD` expiry as the last moment of that day in UTC; null when none was given. */
function endOfDayUtc(value: string): string | null | undefined {
	if (!value) return null;
	if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
	const at = new Date(`${value}T23:59:59.999Z`);
	return Number.isNaN(at.getTime()) ? undefined : at.toISOString();
}

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const base = `${apiUrl()}/admin/tenants/${params.uid}`;
	const [keys, webhook, deliveries] = await Promise.all([
		read<{ keys: ApiKey[]; scopes: string[] }>(fetch, `${base}/api-keys`, locals.accessToken),
		read<Webhook>(fetch, `${base}/webhook`, locals.accessToken),
		read<{ items: WebhookDelivery[] }>(
			fetch,
			`${base}/webhook-deliveries?limit=${DELIVERY_LIMIT}`,
			locals.accessToken
		)
	]);
	return {
		keys: keys.body?.keys ?? [],
		scopes: keys.body?.scopes ?? [],
		keysError: keys.error,
		webhook: webhook.body,
		webhookError: webhook.error,
		deliveries: deliveries.body?.items ?? [],
		deliveriesError: deliveries.error
	};
};

export const actions: Actions = {
	createKey: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'createKey');
		if (refused) return refused;
		const data = await request.formData();
		const name = String(data.get('name') ?? '').trim();
		const scopes = data.getAll('scope').map(String);
		const expiresAt = endOfDayUtc(String(data.get('expires_at') ?? '').trim());
		if (!name) return fail(400, { action: 'createKey', error: 'Give the key a name.' });
		if (scopes.length === 0)
			return fail(400, { action: 'createKey', error: 'Choose at least one scope.' });
		if (expiresAt === undefined)
			return fail(400, { action: 'createKey', error: 'Enter the expiry as a date.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/api-keys`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ name, scopes, expires_at: expiresAt })
		});
		if (!res.ok) return fail(res.status, { action: 'createKey', error: await detail(res) });
		const body = (await res.json()) as { key: ApiKey; secret: string };
		return { action: 'createKey', ok: true, secret: body.secret, key: body.key };
	},

	revokeKey: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'revokeKey');
		if (refused) return refused;
		const keyUid = String((await request.formData()).get('key_uid') ?? '');
		if (!keyUid) return fail(400, { action: 'revokeKey', error: 'Choose a key to revoke.' });
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${params.uid}/api-keys/${encodeURIComponent(keyUid)}/revoke`,
			{ method: 'POST', headers: authHeaders(locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { action: 'revokeKey', error: await detail(res) });
		return { action: 'revokeKey', ok: true };
	},

	setWebhook: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'setWebhook');
		if (refused) return refused;
		const url = String((await request.formData()).get('url') ?? '').trim();
		if (!url) return fail(400, { action: 'setWebhook', error: 'Enter the webhook URL.' });
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/webhook`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ url })
		});
		if (!res.ok) return fail(res.status, { action: 'setWebhook', error: await detail(res) });
		const body = (await res.json()) as { url: string; secret: string };
		return { action: 'setWebhook', ok: true, secret: body.secret };
	},

	removeWebhook: async ({ params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'removeWebhook');
		if (refused) return refused;
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/webhook`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'removeWebhook', error: await detail(res) });
		return { action: 'removeWebhook', ok: true };
	},

	resendDelivery: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'resendDelivery');
		if (refused) return refused;
		const deliveryUid = String((await request.formData()).get('delivery_uid') ?? '');
		if (!deliveryUid)
			return fail(400, { action: 'resendDelivery', error: 'Choose a delivery to send again.' });
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${params.uid}/webhook-deliveries/${encodeURIComponent(deliveryUid)}/resend`,
			{ method: 'POST', headers: authHeaders(locals.accessToken) }
		);
		if (!res.ok) return fail(res.status, { action: 'resendDelivery', error: await detail(res) });
		return { action: 'resendDelivery', ok: true };
	}
};
