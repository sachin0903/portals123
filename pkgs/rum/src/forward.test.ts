import { describe, expect, it, vi } from 'vitest';
import { handleRumRequest } from './forward';
import { beaconBody, RUM_BODY_MAX } from './payload';

const trace = 'ab'.repeat(16);
const span = 'cd'.repeat(8);

describe('handleRumRequest', () => {
	it('forwards the body with the page traceparent', async () => {
		const seen: { url: string; traceparent: string | null; body: string }[] = [];
		const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
			seen.push({
				url: String(url),
				traceparent: new Headers(init?.headers).get('traceparent'),
				body: String(init?.body)
			});
			return new Response(null, { status: 204 });
		});
		const raw = JSON.stringify({
			trace_id: trace,
			span_id: span,
			route: '/accounts/[uid]',
			portal: 'client',
			settle_ms: 12
		});
		const response = await handleRumRequest(
			{ text: async () => raw },
			{ apiUrl: 'http://api:8000', accessToken: 'token', fetch }
		);
		expect(response.status).toBe(204);
		expect(seen).toEqual([
			{
				url: 'http://api:8000/me/rum',
				traceparent: `00-${trace}-${span}-00`,
				body: raw
			}
		]);
	});

	it('records nothing without a session', async () => {
		const fetch = vi.fn();
		const response = await handleRumRequest(
			{ text: async () => '{}' },
			{ apiUrl: 'http://api:8000', accessToken: null, fetch }
		);
		expect(response.status).toBe(204);
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('beaconBody', () => {
	it('drops a body over a few KB', () => {
		expect(beaconBody({ route: 'x'.repeat(RUM_BODY_MAX) })).toBeNull();
		expect(beaconBody({ route: '/me', portal: 'client' })).toContain('/me');
	});
});
