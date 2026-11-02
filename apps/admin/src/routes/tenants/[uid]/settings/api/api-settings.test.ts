/**
 * The firm's API settings: partner-API keys, the check webhook and its recent deliveries.
 *
 * The loader reads three endpoints and a failure in one must leave the other two cards working.
 * Every action refuses a read-only operator before any fetch (the same guard as the rest of the
 * tenant sub-pages, see ../../write-gates.test.ts), sends the API exactly the request it expects,
 * and turns a non-OK answer into `fail(status, { action, error })` with the API's own detail.
 */
import { describe, expect, it } from 'vitest';
import { apiUrl } from '$lib/api';
import { actions, load } from './+page.server';

const TENANT_UID = 't_9f2ab1';
const BASE = () => `${apiUrl()}/admin/tenants/${TENANT_UID}`;

const locals = (permissions: string[]) => ({
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions,
	role: null as string | null
});
const READ_ONLY = locals(['tenants:read']);
const WRITER = locals(['tenants:read', 'tenants:write']);

const request = (fields: Record<string, string | string[]>) => ({
	formData: async () => {
		const data = new FormData();
		for (const [k, v] of Object.entries(fields))
			for (const value of Array.isArray(v) ? v : [v]) data.append(k, value);
		return data;
	}
});

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

type Call = { url: string; init: RequestInit | undefined };

/** A fetch that records every call and answers with `respond`. */
function recorder(respond: (url: string, init?: RequestInit) => Response | Promise<Response>) {
	const calls: Call[] = [];
	const fetch = async (url: string, init?: RequestInit) => {
		calls.push({ url, init });
		return respond(url, init);
	};
	return { calls, fetch };
}

const KEY = {
	uid: 'k_1',
	name: 'CRM integration',
	key_prefix: 'live_ab12',
	scopes: ['clients:read', 'checks:read'],
	status: 'active',
	created_at: '2026-09-01T09:00:00Z',
	last_used_at: null,
	expires_at: null,
	revoked_at: null
};
const SCOPES = ['clients:read', 'clients:write', 'checks:read', 'checks:write', 'links:write'];
const WEBHOOK = {
	url: 'https://firm.example.com/hook',
	created_at: '2026-09-01T09:00:00Z',
	updated_at: '2026-09-02T09:00:00Z'
};
const DELIVERY = {
	uid: 'd_1',
	event: 'check.updated',
	check_uid: 'c_1',
	kind: 'aml',
	check_status: 'clear',
	status: 'failed',
	attempts: 7,
	next_attempt_at: '2026-09-03T09:00:00Z',
	last_error: 'HTTP 500',
	sent_at: null,
	created_at: '2026-09-02T09:00:00Z',
	updated_at: '2026-09-03T09:00:00Z'
};

/* eslint-disable @typescript-eslint/no-explicit-any */
const runLoad = (args: object) => (load as any)(args);
const run = (name: keyof typeof actions, args: object) => (actions[name] as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

describe('the API settings load', () => {
	it('builds the keys, webhook and deliveries cards from the three endpoints', async () => {
		const { calls, fetch } = recorder((url) => {
			if (url === `${BASE()}/api-keys`) return json({ keys: [KEY], scopes: SCOPES });
			if (url === `${BASE()}/webhook`) return json(WEBHOOK);
			if (url === `${BASE()}/webhook-deliveries?limit=50`) return json({ items: [DELIVERY] });
			return json({ detail: 'Not found' }, 404);
		});
		const result = await runLoad({ locals: READ_ONLY, params: { uid: TENANT_UID }, fetch });

		expect(result).toEqual({
			keys: [KEY],
			scopes: SCOPES,
			keysError: null,
			webhook: WEBHOOK,
			webhookError: null,
			deliveries: [DELIVERY],
			deliveriesError: null
		});
		expect(calls.map((c) => c.url).sort()).toEqual(
			[`${BASE()}/api-keys`, `${BASE()}/webhook`, `${BASE()}/webhook-deliveries?limit=50`].sort()
		);
		for (const call of calls)
			expect((call.init?.headers as Record<string, string>).Authorization).toBe('Bearer tok');
	});

	it('keeps the other cards when one endpoint answers non-OK', async () => {
		const { fetch } = recorder((url) => {
			if (url === `${BASE()}/api-keys`) return json({ keys: [KEY], scopes: SCOPES });
			if (url === `${BASE()}/webhook`) return json({ detail: 'Webhook store unavailable.' }, 500);
			return json({ items: [DELIVERY] });
		});
		const result = await runLoad({ locals: READ_ONLY, params: { uid: TENANT_UID }, fetch });

		expect(result.webhook).toBeNull();
		expect(result.webhookError).toBe('Webhook store unavailable.');
		expect(result.keys).toEqual([KEY]);
		expect(result.keysError).toBeNull();
		expect(result.deliveries).toEqual([DELIVERY]);
		expect(result.deliveriesError).toBeNull();
	});

	it('keeps the other cards when one fetch throws', async () => {
		const { fetch } = recorder((url) => {
			if (url.startsWith(`${BASE()}/webhook-deliveries`)) throw new TypeError('fetch failed');
			if (url === `${BASE()}/webhook`)
				return json({ url: null, created_at: null, updated_at: null });
			return json({ keys: [], scopes: SCOPES });
		});
		const result = await runLoad({ locals: READ_ONLY, params: { uid: TENANT_UID }, fetch });

		expect(result.deliveries).toEqual([]);
		expect(result.deliveriesError).toBe('The service is unavailable.');
		expect(result.keys).toEqual([]);
		expect(result.scopes).toEqual(SCOPES);
		expect(result.webhook).toEqual({ url: null, created_at: null, updated_at: null });
	});
});

describe('the API settings actions refuse tenants:read before any fetch', () => {
	const cases: [keyof typeof actions, Record<string, string | string[]>][] = [
		['createKey', { name: 'CRM', scope: ['clients:read'] }],
		['revokeKey', { key_uid: 'k_1' }],
		['setWebhook', { url: 'https://firm.example.com/hook' }],
		['removeWebhook', {}],
		['resendDelivery', { delivery_uid: 'd_1' }]
	];
	for (const [name, fields] of cases) {
		it(`?/${name}`, async () => {
			const { calls, fetch } = recorder(() => json({}));
			const refused = await run(name, {
				locals: READ_ONLY,
				params: { uid: TENANT_UID },
				request: request(fields),
				fetch
			});
			expect(refused).toMatchObject({
				status: 403,
				data: {
					action: name,
					error: 'You need the tenants:write permission to manage this firm.'
				}
			});
			expect(calls).toHaveLength(0);
		});
	}
});

describe('the API settings actions call the API', () => {
	const event = (fields: Record<string, string | string[]>, fetch: unknown) => ({
		locals: WRITER,
		params: { uid: TENANT_UID },
		request: request(fields),
		fetch
	});

	it('?/createKey posts the name, the chosen scopes and an end-of-day UTC expiry', async () => {
		const { calls, fetch } = recorder(() => json({ key: KEY, secret: 'live_secret_123' }, 201));
		const result = await run(
			'createKey',
			event(
				{
					name: '  CRM integration ',
					scope: ['clients:read', 'checks:read'],
					expires_at: '2026-12-31'
				},
				fetch
			)
		);

		expect(calls).toHaveLength(1);
		expect(calls[0]!.url).toBe(`${BASE()}/api-keys`);
		expect(calls[0]!.init?.method).toBe('POST');
		expect((calls[0]!.init?.headers as Record<string, string>)['Content-Type']).toBe(
			'application/json'
		);
		expect(JSON.parse(String(calls[0]!.init?.body))).toEqual({
			name: 'CRM integration',
			scopes: ['clients:read', 'checks:read'],
			expires_at: '2026-12-31T23:59:59.999Z'
		});
		expect(result).toEqual({
			action: 'createKey',
			ok: true,
			secret: 'live_secret_123',
			key: KEY
		});
	});

	it('?/createKey sends a null expiry when none is given', async () => {
		const { calls, fetch } = recorder(() => json({ key: KEY, secret: 'live_x' }, 201));
		await run('createKey', event({ name: 'CRM', scope: 'links:write', expires_at: '' }, fetch));
		expect(JSON.parse(String(calls[0]!.init?.body))).toEqual({
			name: 'CRM',
			scopes: ['links:write'],
			expires_at: null
		});
	});

	it('?/createKey refuses a key with no scopes or no name before calling the API', async () => {
		const { calls, fetch } = recorder(() => json({}));
		expect(await run('createKey', event({ name: 'CRM' }, fetch))).toMatchObject({
			status: 400,
			data: { action: 'createKey' }
		});
		expect(await run('createKey', event({ name: ' ', scope: 'checks:read' }, fetch))).toMatchObject(
			{ status: 400, data: { action: 'createKey' } }
		);
		expect(calls).toHaveLength(0);
	});

	it('?/revokeKey posts to the key’s revoke endpoint', async () => {
		const { calls, fetch } = recorder(() => json({ key: { ...KEY, status: 'revoked' } }));
		const result = await run('revokeKey', event({ key_uid: 'k_1' }, fetch));
		expect(calls).toHaveLength(1);
		expect(calls[0]!.url).toBe(`${BASE()}/api-keys/k_1/revoke`);
		expect(calls[0]!.init?.method).toBe('POST');
		expect(calls[0]!.init?.body).toBeUndefined();
		expect(result).toEqual({ action: 'revokeKey', ok: true });
	});

	it('?/setWebhook puts the URL and returns the signing secret', async () => {
		const { calls, fetch } = recorder(() =>
			json({ url: 'https://firm.example.com/hook', secret: 'whsec_123' })
		);
		const result = await run(
			'setWebhook',
			event({ url: ' https://firm.example.com/hook ' }, fetch)
		);
		expect(calls).toHaveLength(1);
		expect(calls[0]!.url).toBe(`${BASE()}/webhook`);
		expect(calls[0]!.init?.method).toBe('PUT');
		expect(JSON.parse(String(calls[0]!.init?.body))).toEqual({
			url: 'https://firm.example.com/hook'
		});
		expect(result).toEqual({ action: 'setWebhook', ok: true, secret: 'whsec_123' });
	});

	it('?/removeWebhook deletes the webhook', async () => {
		const { calls, fetch } = recorder(() => json({ url: null }));
		const result = await run('removeWebhook', event({}, fetch));
		expect(calls).toHaveLength(1);
		expect(calls[0]!.url).toBe(`${BASE()}/webhook`);
		expect(calls[0]!.init?.method).toBe('DELETE');
		expect(result).toEqual({ action: 'removeWebhook', ok: true });
	});

	it('?/resendDelivery posts to the delivery’s resend endpoint', async () => {
		const { calls, fetch } = recorder(() => json({ delivery: { ...DELIVERY, status: 'pending' } }));
		const result = await run('resendDelivery', event({ delivery_uid: 'd_1' }, fetch));
		expect(calls).toHaveLength(1);
		expect(calls[0]!.url).toBe(`${BASE()}/webhook-deliveries/d_1/resend`);
		expect(calls[0]!.init?.method).toBe('POST');
		expect(calls[0]!.init?.body).toBeUndefined();
		expect(result).toEqual({ action: 'resendDelivery', ok: true });
	});
});

describe('a non-OK API answer becomes fail(status, { action, error })', () => {
	const cases: [keyof typeof actions, Record<string, string | string[]>, number, string][] = [
		['createKey', { name: 'CRM', scope: 'clients:read' }, 422, 'Unknown scope.'],
		['revokeKey', { key_uid: 'k_404' }, 404, 'API key not found.'],
		[
			'setWebhook',
			{ url: 'https://10.0.0.1/hook' },
			422,
			'The URL must resolve to a public address.'
		],
		['removeWebhook', {}, 500, 'Webhook store unavailable.'],
		['resendDelivery', { delivery_uid: 'd_1' }, 409, 'Only a failed delivery can be sent again.']
	];
	for (const [name, fields, status, message] of cases) {
		it(`?/${name} → ${status}`, async () => {
			const { fetch } = recorder(() => json({ detail: message }, status));
			const result = await run(name, {
				locals: WRITER,
				params: { uid: TENANT_UID },
				request: request(fields),
				fetch
			});
			expect(result).toMatchObject({ status, data: { action: name, error: message } });
		});
	}
});
