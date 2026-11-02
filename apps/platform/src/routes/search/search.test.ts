/**
 * The platform search proxy (`+server.ts`).
 *
 * The bar calls this on every keystroke; it exists only because a browser component can't hold the
 * access token. There is deliberately no permission gate here — `search.py` omits any group the
 * caller couldn't open and RLS decides every row — so what this test pins is that the proxy adds
 * nothing and hides nothing:
 *
 * - a signed-out caller is refused before the API is touched;
 * - the caller's own bearer token is forwarded, so the API scopes to *their* claims (the previous
 *   bar had no request at all, and a proxy that dropped the token would be a firm-wide search under
 *   whatever the API defaulted to);
 * - an upstream failure surfaces as a failure, never as an empty result that reads as "no matches".
 */

import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const STAFF = { user: { id: 'u-adviser', email: 'adviser@firm.test' }, accessToken: 'tok' };

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
				? 'http://platform.test/search'
				: `http://platform.test/search?q=${encodeURIComponent(q)}${scope ? `&scope=${encodeURIComponent(scope)}` : ''}`
		),
		locals,
		fetch
	} as unknown as Event);

describe('platform search proxy', () => {
	it("forwards the query and the caller's own token to the cross-entity /search", async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		const res = await run(STAFF, 'cole & co', fetch);
		expect(await res.json()).toEqual(RESULTS);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search?q=cole%20%26%20co&limit=5');
		// Without this the API would not be answering as this adviser, and the book scoping the
		// whole design rests on would be someone else's.
		expect(calls[0].init?.headers).toEqual({ Authorization: 'Bearer tok' });
	});

	it('re-encodes rather than pasting the raw query into the upstream URL', async () => {
		// A query containing & or # would otherwise truncate or inject an upstream parameter.
		const { fetch, calls } = stubFetch(RESULTS);
		await run(STAFF, 'a#b&limit=999', fetch);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search?q=a%23b%26limit%3D999&limit=5');
	});

	it('forwards a recognised scope and drops an unknown one', async () => {
		const scoped = stubFetch(RESULTS);
		await run(STAFF, 'cole', scoped.fetch, 'clients');
		expect(scoped.calls[0].url).toBe('http://127.0.0.1:8000/search?q=cole&limit=5&scope=clients');

		const unknown = stubFetch(RESULTS);
		await run(STAFF, 'cole', unknown.fetch, 'settings');
		expect(unknown.calls[0].url).toBe('http://127.0.0.1:8000/search?q=cole&limit=5');
	});

	it('treats a missing q as an empty query rather than the string "null"', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await run(STAFF, null, fetch);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search?q=&limit=5');
	});

	it('refuses a signed-out caller before it touches the API', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await expect(run({ user: null, accessToken: null }, 'cole', fetch)).rejects.toMatchObject({
			status: 401
		});
		expect(calls).toEqual([]);
	});

	it('surfaces an upstream failure as that status, never as an empty result', async () => {
		for (const status of [401, 403, 500, 503]) {
			const { fetch } = stubFetch({ detail: 'boom' }, status);
			await expect(run(STAFF, 'cole', fetch)).rejects.toMatchObject({ status });
		}
	});
});
