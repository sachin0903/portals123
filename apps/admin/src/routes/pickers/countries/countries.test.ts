/**
 * The same-origin countries picker: the browser never holds the access token, so this forwards
 * with the session and keeps the list for the process lifetime.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { GET, _resetCountriesCache } from './+server';

const locals = { user: { id: 'u-ops' }, accessToken: 'tok' };

function json(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

afterEach(() => _resetCountriesCache());

describe('GET /pickers/countries', () => {
	it('refuses a visitor who is not signed in, without calling the API', async () => {
		let hits = 0;
		const fetch = (async () => {
			hits += 1;
			return json([]);
		}) as unknown as typeof globalThis.fetch;
		const res = await GET({ locals: { user: null, accessToken: null }, fetch } as never);
		expect(res.status).toBe(401);
		expect(hits).toBe(0);
	});

	it('forwards with the session token and caches the list', async () => {
		const calls: { url: string; auth: string | null }[] = [];
		const fetch = (async (url: string, init?: RequestInit) => {
			calls.push({
				url: String(url),
				auth: new Headers(init?.headers).get('authorization')
			});
			return json([{ code: 'GB', name: 'United Kingdom' }]);
		}) as unknown as typeof globalThis.fetch;
		const first = await GET({ locals, fetch } as never);
		const second = await GET({ locals, fetch } as never);
		expect(calls).toHaveLength(1);
		expect(calls[0].url).toMatch(/\/ref\/countries$/);
		expect(calls[0].auth).toBe('Bearer tok');
		expect(await first.json()).toEqual([{ code: 'GB', name: 'United Kingdom' }]);
		expect(await second.json()).toEqual([{ code: 'GB', name: 'United Kingdom' }]);
	});

	it('passes a refusal through and caches nothing', async () => {
		let hits = 0;
		const fetch = (async () => {
			hits += 1;
			return json({ detail: 'Not authorised' }, 403);
		}) as unknown as typeof globalThis.fetch;
		const res = await GET({ locals, fetch } as never);
		expect(res.status).toBe(403);
		expect(await res.json()).toEqual({ error: 'Not authorised' });
		await GET({ locals, fetch } as never);
		expect(hits).toBe(2);
	});
});
