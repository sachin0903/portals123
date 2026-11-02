/**
 * The two shell-image relays: a content-hashed, immutable name gets a year-long public cache.
 *
 * The Graphene logo is painted on every admin page, so the second page view must never fetch it
 * again. The API sets `public, max-age=31536000, immutable` on `/internal/assets/{name}` and the
 * relay forwards it; this pins both the forwarded header and the fallback the relay applies when
 * the upstream says nothing.
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal-token' }
}));

import { GET as byName } from './[name]/+server';
import { GET as byTenant } from './[tenant]/[name]/+server';

const IMMUTABLE = 'public, max-age=31536000, immutable';
const NAME = 'logo-0123456789abcdef.png';

function upstream(headers: Record<string, string> = {}) {
	return vi.fn(
		async () =>
			new Response(new Uint8Array(16), {
				status: 200,
				headers: { 'content-type': 'image/png', ...headers }
			})
	);
}

type Handler = (event: unknown) => Promise<Response>;

describe.each<[string, Handler, Record<string, string>]>([
	['_assets/[name]', byName as unknown as Handler, { name: NAME }],
	['_assets/[tenant]/[name]', byTenant as unknown as Handler, { tenant: 'ten_abc', name: NAME }]
])('%s', (_label, handler, params) => {
	it('forwards the API’s immutable cache header', async () => {
		const res = await handler({ params, fetch: upstream({ 'cache-control': IMMUTABLE }) });
		expect(res.status).toBe(200);
		expect(res.headers.get('cache-control')).toBe(IMMUTABLE);
	});

	it('applies the same long cache when the upstream sends none', async () => {
		const res = await handler({ params, fetch: upstream() });
		expect(res.headers.get('cache-control')).toBe(IMMUTABLE);
		expect(res.headers.get('x-content-type-options')).toBe('nosniff');
	});
});
