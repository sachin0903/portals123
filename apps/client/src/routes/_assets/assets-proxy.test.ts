/**
 * The branding-asset relay: a content-hashed, immutable name gets a year-long public cache.
 *
 * The firm logo is painted on every page, so the second page view must never fetch it again.
 * The API sets `public, max-age=31536000, immutable` on `/internal/assets/{name}` and the relay
 * forwards it; this pins both the forwarded header and the fallback the relay applies when the
 * upstream says nothing.
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal-token' }
}));

import { GET } from './[name]/+server';

const IMMUTABLE = 'public, max-age=31536000, immutable';

function upstream(headers: Record<string, string> = {}) {
	return vi.fn(
		async () =>
			new Response(new Uint8Array(16), {
				status: 200,
				headers: { 'content-type': 'image/png', ...headers }
			})
	);
}

async function call(fetch: unknown) {
	return (GET as unknown as (event: unknown) => Promise<Response>)({
		params: { name: 'logo-0123456789abcdef.png' },
		fetch,
		request: new Request('http://app.firm.test/_assets/x', { headers: { host: 'app.firm.test' } })
	});
}

describe('_assets/[name]', () => {
	it('forwards the API’s immutable cache header', async () => {
		const res = await call(upstream({ 'cache-control': IMMUTABLE }));
		expect(res.status).toBe(200);
		expect(res.headers.get('cache-control')).toBe(IMMUTABLE);
	});

	it('applies the same long cache when the upstream sends none', async () => {
		const res = await call(upstream());
		expect(res.headers.get('cache-control')).toBe(IMMUTABLE);
		expect(res.headers.get('x-content-type-options')).toBe('nosniff');
	});
});
