/**
 * The operations search proxy (`+server.ts`).
 *
 * This is the only thing between the browser and a cross-tenant firm list, and the bar calls it on
 * every keystroke — so its gate is worth a test of its own rather than being inferred from the
 * component. The cases that matter:
 *
 * - it refuses a caller without `tenants:read`, and cannot be talked round by a role a firm named
 *   "admin" or "system" (the bypass `can` was deliberately built to ignore);
 * - it never invents a body: an upstream failure comes back as that status, so the palette shows
 *   its retry banner instead of an empty list that reads as "this instance has no firms";
 * - it forwards the query encoded, and pins the `limit` the palette is sized for.
 */

import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

type Locals = {
	user: unknown;
	accessToken: string | null;
	permissions: string[];
	role: string | null;
};

const OPS: Locals = {
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions: ['tenants:read'],
	role: 'reviewer'
};

function stubFetch(body: unknown, status = 200) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		calls.push({ url: String(input), init });
		return {
			ok: status < 400,
			status,
			json: async () => body
		} as unknown as Response;
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

const RESULTS = {
	query: 'ash',
	groups: [
		{ type: 'tenant', label: 'Firms', hits: [{ type: 'tenant', id: '4', title: 'Ashworth' }] }
	]
};

type Event = Parameters<typeof GET>[0];

const run = (locals: Partial<Locals>, q: string, fetch: typeof globalThis.fetch) =>
	GET({
		url: new URL(`http://ops.test/search?q=${encodeURIComponent(q)}`),
		locals,
		fetch
	} as unknown as Event);

describe('operations search proxy', () => {
	it('forwards the query to /search/tenants with the bearer token and the palette limit', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		const res = await run(OPS, 'ash & co', fetch);
		expect(await res.json()).toEqual(RESULTS);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search/tenants?q=ash%20%26%20co&limit=8');
		expect(calls[0].init?.headers).toEqual({ Authorization: 'Bearer tok' });
	});

	it('treats a missing q as an empty query rather than the string "null"', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await GET({
			url: new URL('http://ops.test/search'),
			locals: OPS,
			fetch
		} as unknown as Event);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search/tenants?q=&limit=8');
	});

	it('refuses an unauthenticated caller before it touches the API', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await expect(run({ ...OPS, user: null }, 'ash', fetch)).rejects.toMatchObject({ status: 401 });
		expect(calls).toEqual([]);
	});

	it('refuses an ops user without tenants:read', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await expect(
			run({ ...OPS, permissions: ['audit:read', 'operations:write'] }, 'ash', fetch)
		).rejects.toMatchObject({ status: 403 });
		expect(calls).toEqual([]);
	});

	it('cannot be bypassed by a role name, or by a look-alike slug', async () => {
		const { fetch } = stubFetch(RESULTS);
		for (const role of ['admin', 'system', 'owner', 'superuser']) {
			await expect(run({ ...OPS, permissions: [], role }, 'ash', fetch)).rejects.toMatchObject({
				status: 403
			});
		}
		for (const slug of ['tenants:rea', 'tenant:read', 'TENANTS:READ', 'tenants:read_all']) {
			await expect(
				run({ ...OPS, permissions: [slug], role: null }, 'ash', fetch)
			).rejects.toMatchObject({ status: 403 });
		}
	});

	it('lets tenants:write and system through (write implies read; system bypasses)', async () => {
		for (const permissions of [['tenants:write'], ['system']]) {
			const { fetch } = stubFetch(RESULTS);
			const res = await run({ ...OPS, permissions }, 'ash', fetch);
			expect(await res.json()).toEqual(RESULTS);
		}
	});

	it('surfaces an upstream failure as that status, never as an empty firm list', async () => {
		const { fetch } = stubFetch({ detail: 'boom' }, 503);
		await expect(run(OPS, 'ash', fetch)).rejects.toMatchObject({ status: 503 });
	});

	it('passes an upstream 403 through rather than swallowing it into a 200', async () => {
		// The API re-checks tenants:read; if the two ever disagree the palette must still say
		// "unavailable" instead of showing nothing found.
		const { fetch } = stubFetch({ detail: 'Not permitted' }, 403);
		await expect(run(OPS, 'ash', fetch)).rejects.toMatchObject({ status: 403 });
	});
});
