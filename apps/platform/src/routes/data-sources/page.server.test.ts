/**
 * The data-sources loader: the gate is answered before the first byte, the list behind it, and a
 * failed read is stated rather than rendered as "no custodians are connected".
 */
import { describe, expect, it } from 'vitest';
import { load, type SourcesFeed } from './+page.server';

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

const run = async (permissions: string[], fetch: typeof globalThis.fetch) =>
	(await load({
		locals: { user: { id: 'u_1' }, accessToken: 'tok', permissions, role: null },
		fetch
	} as never)) as { feed: Promise<SourcesFeed> };

describe('the data-sources loader', () => {
	it('refuses without data_sources:read, before touching the API', async () => {
		let calls = 0;
		const fetch = (async () => {
			calls++;
			return json([]);
		}) as typeof globalThis.fetch;
		await expect(run(['contacts:read'], fetch)).rejects.toMatchObject({ status: 403 });
		expect(calls).toBe(0);
	});

	it('streams the sources so the first byte does not wait on the read', async () => {
		const fetch = (async () => json([{ key: 'sei', name: 'SEI' }])) as typeof globalThis.fetch;
		const result = await run(['data_sources:read'], fetch);
		expect(result.feed).toBeInstanceOf(Promise);
		await expect(result.feed).resolves.toEqual({
			sources: [{ key: 'sei', name: 'SEI' }],
			loadError: null
		});
	});

	it('states a failure instead of an empty list', async () => {
		const fetch = (async () =>
			json({ detail: 'warehouse unreachable' }, 503)) as typeof globalThis.fetch;
		const result = await run(['data_sources:read'], fetch);
		await expect(result.feed).resolves.toMatchObject({
			sources: [],
			loadError: expect.any(String)
		});
	});

	it('turns a thrown fetch into a stated failure, never a rejected stream', async () => {
		const fetch = (async () => {
			throw new TypeError('fetch failed');
		}) as typeof globalThis.fetch;
		const result = await run(['data_sources:read'], fetch);
		await expect(result.feed).resolves.toMatchObject({
			sources: [],
			loadError: expect.any(String)
		});
	});
});
