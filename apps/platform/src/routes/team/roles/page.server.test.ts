import { describe, expect, it } from 'vitest';
import { load, type RoleCatalogue } from './+page.server';

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

const locals = (permissions: string[]) => ({
	user: { id: 'staff' },
	accessToken: 'token',
	permissions,
	role: 'administrator'
});

type Loaded = { authorised: boolean; canManage: boolean; catalogue: Promise<RoleCatalogue> | null };
const run = async (permissions: string[], fetch: typeof globalThis.fetch) =>
	(await load({ locals: locals(permissions), fetch } as never)) as Loaded;

describe('Roles loader', () => {
	it('answers the permission synchronously and streams the catalogue', async () => {
		let calls = 0;
		const fetch = (async () => {
			calls++;
			return json([]);
		}) as typeof globalThis.fetch;
		const result = await run(['roles:read', 'roles:write'], fetch);
		expect(result.authorised).toBe(true);
		expect(result.canManage).toBe(true);
		// The first byte does not wait on the API: the catalogue is a promise the page reads.
		expect(result.catalogue).toBeInstanceOf(Promise);
		await expect(result.catalogue).resolves.toMatchObject({
			roles: [],
			permissions: [],
			forbidden: false,
			loadError: false
		});
		expect(calls).toBe(2);
	});

	it('does not touch the API for a caller without roles:read', async () => {
		let calls = 0;
		const fetch = (async () => {
			calls++;
			return json([]);
		}) as typeof globalThis.fetch;
		const result = await run(['contacts:read'], fetch);
		expect(result).toEqual({ authorised: false, canManage: false, catalogue: null });
		expect(calls).toBe(0);
	});

	it('reports a permission-catalogue failure instead of presenting an empty catalogue', async () => {
		const fetch = (async (url: string | URL) =>
			String(url).endsWith('/firm/permissions')
				? new Response('unavailable', { status: 503 })
				: json([])) as typeof globalThis.fetch;

		const result = await run(['roles:read'], fetch);
		expect(result.authorised).toBe(true);
		await expect(result.catalogue).resolves.toMatchObject({
			loadError: true,
			permissions: []
		});
	});

	it('relays the API’s own refusal as forbidden, not as an empty editor', async () => {
		const fetch = (async () => new Response('no', { status: 403 })) as typeof globalThis.fetch;
		const result = await run(['roles:read'], fetch);
		await expect(result.catalogue).resolves.toMatchObject({ forbidden: true, loadError: false });
	});

	it('turns a thrown fetch into a banner, never a rejected stream', async () => {
		const fetch = (async () => {
			throw new TypeError('fetch failed');
		}) as typeof globalThis.fetch;
		const result = await run(['roles:read'], fetch);
		await expect(result.catalogue).resolves.toMatchObject({ loadError: true, forbidden: false });
	});
});
