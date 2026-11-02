/**
 * The in-firm book search proxy (`tenants/[uid]/search/+server.ts`).
 *
 * Pins `?tenant=` to the route firm and forwards the caller's bearer token. The bar hides itself
 * without `tenant_portal:read`, so the proxy must refuse the same callers.
 */

import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const OPS = {
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions: ['tenant_portal:read'],
	role: null
};

function stubFetch(body: unknown, status = 200) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		calls.push({ url: String(input), init });
		return { ok: status < 400, status, json: async () => body } as unknown as Response;
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

const RESULTS = { query: 'cole', groups: [], truncated: false };

type Event = Parameters<typeof GET>[0];

const run = (
	locals: object,
	q: string | null,
	fetch: typeof globalThis.fetch,
	scope: string | null = null
) =>
	GET({
		url: new URL(
			q === null
				? 'http://ops.test/tenants/ten_acme/search'
				: `http://ops.test/tenants/ten_acme/search?q=${encodeURIComponent(q)}${scope ? `&scope=${encodeURIComponent(scope)}` : ''}`
		),
		params: { uid: 'ten_acme' },
		locals,
		fetch
	} as unknown as Event);

describe('admin firm-book search proxy', () => {
	it('forwards the query with ?tenant= pinned to the route firm', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		const res = await run(OPS, 'cole & co', fetch);
		expect(await res.json()).toEqual(RESULTS);
		expect(calls[0].url).toContain('/search?');
		expect(calls[0].url).toContain('tenant=ten_acme');
		expect(calls[0].url).toContain('q=cole+%26+co');
		expect(calls[0].url).toContain('limit=5');
		expect(calls[0].init?.headers).toEqual({ Authorization: 'Bearer tok' });
	});

	it('forwards a recognised scope and drops an unknown one', async () => {
		const scoped = stubFetch(RESULTS);
		await run(OPS, 'cole', scoped.fetch, 'clients');
		expect(scoped.calls[0].url).toContain('scope=clients');

		const unknown = stubFetch(RESULTS);
		await run(OPS, 'cole', unknown.fetch, 'settings');
		expect(unknown.calls[0].url).not.toContain('scope=');
	});

	it('refuses a signed-out caller before touching the API', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await expect(run({ user: null, accessToken: null }, 'cole', fetch)).rejects.toMatchObject({
			status: 401
		});
		expect(calls).toHaveLength(0);
	});

	it('refuses a caller without tenant_portal:read', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await expect(
			run({ ...OPS, permissions: ['tenants:read'] }, 'cole', fetch)
		).rejects.toMatchObject({ status: 403 });
		expect(calls).toHaveLength(0);
	});

	it('surfaces an upstream failure rather than an empty result', async () => {
		const { fetch } = stubFetch({}, 503);
		await expect(run(OPS, 'cole', fetch)).rejects.toMatchObject({ status: 503 });
	});
});
