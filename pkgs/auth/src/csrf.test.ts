import { describe, expect, it } from 'vitest';
import { crossSiteRefusal, isCrossSiteMutation } from './csrf';

const ORIGIN = 'https://portal.firm.test';
const req = (method: string, headers: Record<string, string> = {}) =>
	new Request(`${ORIGIN}/messages/send`, { method, headers });

describe('isCrossSiteMutation', () => {
	it('never judges a safe method', () => {
		expect(isCrossSiteMutation(req('GET', { 'sec-fetch-site': 'cross-site' }), ORIGIN)).toBe(false);
		expect(isCrossSiteMutation(req('HEAD', { origin: 'https://evil.test' }), ORIGIN)).toBe(false);
	});

	it('trusts Sec-Fetch-Site when the browser sends it', () => {
		expect(isCrossSiteMutation(req('POST', { 'sec-fetch-site': 'same-origin' }), ORIGIN)).toBe(
			false
		);
		expect(isCrossSiteMutation(req('POST', { 'sec-fetch-site': 'none' }), ORIGIN)).toBe(false);
		expect(isCrossSiteMutation(req('POST', { 'sec-fetch-site': 'same-site' }), ORIGIN)).toBe(true);
		expect(isCrossSiteMutation(req('POST', { 'sec-fetch-site': 'cross-site' }), ORIGIN)).toBe(true);
		// Sec-Fetch-Site wins over a matching Origin — the page cannot forge the former.
		expect(
			isCrossSiteMutation(req('PUT', { 'sec-fetch-site': 'cross-site', origin: ORIGIN }), ORIGIN)
		).toBe(true);
	});

	it('falls back to Origin', () => {
		expect(isCrossSiteMutation(req('POST', { origin: ORIGIN }), ORIGIN)).toBe(false);
		expect(isCrossSiteMutation(req('POST', { origin: 'https://evil.test' }), ORIGIN)).toBe(true);
		expect(isCrossSiteMutation(req('DELETE', { origin: 'null' }), ORIGIN)).toBe(true);
	});

	it('does not judge a request with neither header (no browser, no ambient credentials to lend)', () => {
		expect(isCrossSiteMutation(req('POST'), ORIGIN)).toBe(false);
	});
});

describe('crossSiteRefusal', () => {
	it('is a 403 JSON, never cached', async () => {
		const res = crossSiteRefusal();
		expect(res.status).toBe(403);
		expect(await res.json()).toEqual({ error: 'cross_site' });
		expect(res.headers.get('cache-control')).toBe('no-store');
	});
});
