/**
 * The client portal's search proxy (`+server.ts`).
 *
 * The Spotlight palette calls this on every keystroke. The scoping is entirely the API's — `search.py`
 * derives "is this a client end-user" from the caller's own claims — so the one thing this proxy
 * must never do is send a request that isn't the caller's. What is pinned here:
 *
 * - a signed-out caller is refused before the API is touched;
 * - the caller's own bearer token is forwarded (drop it and the API would answer as somebody else,
 *   which on this portal means another household's documents);
 * - the query is re-encoded, so a typed `&` can't smuggle an upstream parameter;
 * - an upstream failure surfaces as a failure, never as an empty result reading as "no matches".
 */

import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const CLIENT = { user: { id: 'u-client', email: 'jo@example.test' }, accessToken: 'tok' };

function stubFetch(body: unknown, status = 200) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
		calls.push({ url: String(input), init });
		return { ok: status < 400, status, json: async () => body } as unknown as Response;
	});
	return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

const RESULTS = { query: 'isa', groups: [], truncated: false };

type Event = Parameters<typeof GET>[0];

const run = (locals: object, q: string | null, fetch: typeof globalThis.fetch) =>
	GET({
		url: new URL(
			q === null
				? 'http://portal.test/search'
				: `http://portal.test/search?q=${encodeURIComponent(q)}`
		),
		locals,
		fetch
	} as unknown as Event);

describe('client search proxy', () => {
	it("forwards the query and the caller's own token to /search", async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		const res = await run(CLIENT, 'isa review', fetch);
		expect(await res.json()).toEqual(RESULTS);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search?q=isa%20review&limit=6');
		expect(calls[0].init?.headers).toEqual({ Authorization: 'Bearer tok' });
	});

	it('re-encodes rather than pasting the raw query into the upstream URL', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await run(CLIENT, 'a#b&limit=999', fetch);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search?q=a%23b%26limit%3D999&limit=6');
	});

	it('treats a missing q as an empty query rather than the string "null"', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await run(CLIENT, null, fetch);
		expect(calls[0].url).toBe('http://127.0.0.1:8000/search?q=&limit=6');
	});

	it('refuses a signed-out caller before it touches the API', async () => {
		const { fetch, calls } = stubFetch(RESULTS);
		await expect(run({ user: null, accessToken: null }, 'isa', fetch)).rejects.toMatchObject({
			status: 401
		});
		expect(calls).toEqual([]);
	});

	it('surfaces an upstream failure as that status, never as an empty result', async () => {
		for (const status of [401, 403, 500, 503]) {
			const { fetch } = stubFetch({ detail: 'boom' }, status);
			await expect(run(CLIENT, 'isa', fetch)).rejects.toMatchObject({ status });
		}
	});
});

describe('the active client', () => {
	it('is forwarded, so the palette searches the client on screen', async () => {
		// Un-narrowed, a multi-client login was offered the other clients' documents and action
		// items — and a hit names no owner, so opening it was the only way to find out whose it was.
		const { fetch, calls } = stubFetch({ query: 'isa', groups: [] });
		await GET({
			locals: CLIENT,
			url: new URL('http://portal.test/search?q=isa&contact_id=12'),
			fetch
		} as unknown as Parameters<typeof GET>[0]);
		expect(calls[0].url).toContain('contact_id=12');
	});

	it('is omitted when the bar did not name one', async () => {
		const { fetch, calls } = stubFetch({ query: 'isa', groups: [] });
		await GET({
			locals: CLIENT,
			url: new URL('http://portal.test/search?q=isa'),
			fetch
		} as unknown as Parameters<typeof GET>[0]);
		expect(calls[0].url).not.toContain('contact_id');
	});

	it('cannot be smuggled in as junk — the API types it an int', async () => {
		const { fetch, calls } = stubFetch({ query: 'isa', groups: [] });
		await GET({
			locals: CLIENT,
			url: new URL('http://portal.test/search?q=isa&contact_id=7;drop'),
			fetch
		} as unknown as Parameters<typeof GET>[0]);
		expect(calls[0].url).not.toContain('contact_id');
	});
});
