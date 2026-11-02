/**
 * Same-origin picker proxies — the browser must not hold the access token, so these
 * forward with the session and (for countries) cache the vocabulary for the process lifetime.
 */
import { describe, expect, it } from 'vitest';
import { GET as getMembers } from './members/+server';
import { GET as getModels } from './models/+server';
import { GET as getCountries, _resetCountriesCache } from './countries/+server';
import { GET as getClients } from './clients/+server';

const locals = { user: { id: 'u1' }, accessToken: 'tok' };

function json(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

describe('picker proxies', () => {
	it('forwards members with the session token', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string) => {
			urls.push(String(url));
			return json([{ user_id: 'u', name: 'Ada' }]);
		}) as unknown as typeof globalThis.fetch;
		const res = await getMembers({ locals, fetch } as never);
		expect(urls[0]).toContain('/firm/members');
		expect(await res.json()).toEqual([{ user_id: 'u', name: 'Ada' }]);
	});

	it('forwards models with the session token', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string) => {
			urls.push(String(url));
			return json([{ id: 1, name: 'Balanced' }]);
		}) as unknown as typeof globalThis.fetch;
		const res = await getModels({ locals, fetch } as never);
		expect(urls[0]).toContain('/crm/models');
		expect(await res.json()).toEqual([{ id: 1, name: 'Balanced' }]);
	});

	it('caches countries for the process lifetime', async () => {
		_resetCountriesCache();
		let hits = 0;
		const fetch = (async () => {
			hits += 1;
			return json([{ code: 'GB', name: 'United Kingdom' }]);
		}) as unknown as typeof globalThis.fetch;
		const first = await getCountries({ locals, fetch } as never);
		const second = await getCountries({ locals, fetch } as never);
		expect(hits).toBe(1);
		expect(await first.json()).toEqual([{ code: 'GB', name: 'United Kingdom' }]);
		expect(await second.json()).toEqual([{ code: 'GB', name: 'United Kingdom' }]);
		_resetCountriesCache();
	});

	it('forwards a client typeahead query', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string) => {
			urls.push(String(url));
			return json([]);
		}) as unknown as typeof globalThis.fetch;
		await getClients({
			locals,
			fetch,
			url: new URL('http://portal.test/pickers/clients?q=ada')
		} as never);
		expect(urls[0]).toContain('/crm/contacts');
		expect(urls[0]).toContain('q=ada');
		expect(urls[0]).toContain('limit=50');
	});

	it('refuses an unsigned-in caller', async () => {
		const res = await getMembers({
			locals: { user: null, accessToken: null },
			fetch: (() => {
				throw new Error('must not call the API');
			}) as never
		} as never);
		expect(res.status).toBe(401);
	});
});
