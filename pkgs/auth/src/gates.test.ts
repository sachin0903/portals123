import { describe, expect, it } from 'vitest';
import { decideGate, gateRefusal, isNavigationRequest } from './gates';

describe('decideGate', () => {
	const base = {
		accessToken: 'tok',
		portal: 'client' as string | null | undefined,
		thisPortal: 'client'
	};

	it('allows a confirmed same-portal session', () => {
		expect(decideGate(base)).toBe('allow');
	});

	it('does not gate a signed-out request — that is the loads\u2019 login redirect', () => {
		expect(decideGate({ ...base, accessToken: null, portal: 'platform' })).toBe('allow');
	});

	it('refuses a session confirmed for another portal', () => {
		expect(decideGate({ ...base, portal: 'platform' })).toBe('wrong_portal');
		expect(decideGate({ ...base, portal: null })).toBe('wrong_portal');
	});

	it('fails OPEN on portal when GET /me never answered', () => {
		expect(decideGate({ ...base, portal: undefined })).toBe('allow');
	});
});

describe('isNavigationRequest', () => {
	const req = (headers: Record<string, string>) => new Request('http://p.test/x', { headers });

	it('treats a document load and a SvelteKit data request as navigation', () => {
		expect(isNavigationRequest(req({ 'sec-fetch-dest': 'document' }), false)).toBe(true);
		expect(isNavigationRequest(req({ accept: 'text/html,application/xhtml+xml' }), false)).toBe(
			true
		);
		expect(isNavigationRequest(req({ accept: '*/*' }), true)).toBe(true);
	});

	it('treats a script\u2019s fetch to an endpoint as not', () => {
		expect(isNavigationRequest(req({ 'sec-fetch-dest': 'empty', accept: '*/*' }), false)).toBe(
			false
		);
		expect(isNavigationRequest(req({ accept: 'application/json' }), false)).toBe(false);
		// EventSource: dest `empty`, accept `text/event-stream`.
		expect(
			isNavigationRequest(req({ 'sec-fetch-dest': 'empty', accept: 'text/event-stream' }), false)
		).toBe(false);
	});
});

describe('gateRefusal', () => {
	it('is a 403 JSON naming the reason, never cached', async () => {
		const res = gateRefusal('wrong_portal');
		expect(res.status).toBe(403);
		expect(res.headers.get('cache-control')).toBe('no-store');
		expect(await res.json()).toEqual({ error: 'wrong_portal' });
	});
});
