import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const locals = { user: {}, permissions: ['documents:read'], role: null, accessToken: 't' };
const PREVIEW_CACHE = 'private, max-age=300';

async function call(search: string, upstream: Response) {
	const fetch = vi.fn<(url: string) => Promise<Response>>(async () => upstream);
	const url = new URL(`http://x/documents/doc_1/content${search}`);
	const res = (await GET({ params: { uid: 'doc_1' }, url, locals, fetch } as never)) as Response;
	return { res, upstreamUrl: String(fetch.mock.calls[0]?.[0]) };
}

describe('GET /documents/[uid]/content', () => {
	it.each([
		{ search: '?preview=1', path: /doc_1\/content\?preview=true$/, cache: PREVIEW_CACHE },
		{ search: '', path: /doc_1\/content$/, cache: undefined }
	])('"$search" keeps the API cache policy, else never caches', async ({ search, path, cache }) => {
		const headers: Record<string, string> = { 'content-type': 'application/pdf' };
		if (cache) headers['cache-control'] = cache;
		const { res, upstreamUrl } = await call(search, new Response('%PDF', { headers }));
		expect(upstreamUrl).toMatch(path);
		expect(res.headers.get('cache-control')).toBe(cache ?? 'private, no-store');
	});

	it('keeps the sandbox headers on served HTML', async () => {
		const sandbox = { 'content-security-policy': 'sandbox', 'x-content-type-options': 'nosniff' };
		const headers = { 'content-type': 'text/html', ...sandbox };
		const { res } = await call('', new Response('<p>I agree</p>', { headers }));
		for (const [name, value] of Object.entries(sandbox)) expect(res.headers.get(name)).toBe(value);
	});

	it('passes a bodiless 204 preview through', async () => {
		const headers = { 'cache-control': PREVIEW_CACHE };
		const { res } = await call('?preview=1', new Response(null, { status: 204, headers }));
		expect(res.status).toBe(204);
		expect(res.headers.get('cache-control')).toBe(PREVIEW_CACHE);
	});
});
