import { describe, expect, it } from 'vitest';
import { exportFilename } from '$lib/flow-bundle';
import { GET } from './+server';

const BUNDLE = {
	format: 'graphene.flow',
	version: 1,
	flow: { code: 'fact_find', name: 'Fact find' },
	definition: { nodes: [] }
};

function fetchFor(status: number, body: unknown) {
	const seen = { url: '', auth: '' };
	const fetch = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
		seen.url = String(input);
		seen.auth = String((init?.headers as Record<string, string> | undefined)?.Authorization ?? '');
		return new Response(JSON.stringify(body), {
			status,
			headers: { 'Content-Type': 'application/json' }
		});
	};
	return { fetch, seen };
}

const event = (fetch: typeof globalThis.fetch, permissions: string[] = ['flows:read']) =>
	({
		locals: { user: { id: 'user_1' }, accessToken: 'token', permissions, role: null },
		params: { uid: 'flw_1' },
		url: new URL('http://test/flows/flw_1/export'),
		fetch
	}) as unknown as Parameters<typeof GET>[0];

describe('flow export download', () => {
	it('names the file after the flow code', () => {
		expect(exportFilename('fact_find')).toBe('fact_find.flow.json');
	});

	it('proxies the API bundle as an attachment named after the code', async () => {
		const { fetch, seen } = fetchFor(200, BUNDLE);
		const response = await GET(event(fetch));
		expect(seen.url).toMatch(/\/flows\/flw_1\/export$/);
		expect(seen.auth).toBe('Bearer token');
		expect(response.headers.get('content-disposition')).toBe(
			'attachment; filename="fact_find.flow.json"'
		);
		expect(response.headers.get('content-type')).toMatch(/^application\/json/);
		expect(await response.json()).toEqual(BUNDLE);
	});

	it('is a 404 when the flow is not visible', async () => {
		const { fetch } = fetchFor(404, { detail: 'Not found' });
		await expect(GET(event(fetch))).rejects.toMatchObject({ status: 404 });
	});

	it('needs flows:read', async () => {
		const { fetch, seen } = fetchFor(200, BUNDLE);
		await expect(GET(event(fetch, []))).rejects.toMatchObject({ status: 403 });
		expect(seen.url).toBe('');
	});
});
