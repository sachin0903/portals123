/**
 * The phone spends the one-time code server-side. A code that has already been used, or a
 * session the API no longer recognises, must not be turned into a page that can upload.
 */

import { describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal-token' }
}));

const { redeemPhoneLink, loadPhoneCapture } = await import('./identity-phone');

const ok = (body: unknown, status = 200) =>
	({ ok: status < 400, status, json: async () => body }) as Response;
const fail = (status: number) => ({ ok: false, status, json: async () => ({}) }) as Response;

describe('redeeming a phone link', () => {
	it('exchanges the code for a token and how long the page may stay open', async () => {
		const fetch = vi.fn(async () => ok({ token: 'idvphone_secret', seconds_remaining: 1700 }));

		const redeemed = await redeemPhoneLink('once', {
			internalToken: 'internal-token',
			fetch: fetch as unknown as typeof globalThis.fetch
		});

		expect(redeemed).toEqual({ token: 'idvphone_secret', secondsRemaining: 1700 });
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://api.test/identity-phone/redeem');
		expect(init.method).toBe('POST');
		expect(new Headers(init.headers).get('x-internal-token')).toBe('internal-token');
		expect(JSON.parse(String(init.body))).toEqual({ code: 'once' });
	});

	it('treats a used or expired code as nothing to open', async () => {
		const fetch = vi.fn(async () => fail(404)) as unknown as typeof globalThis.fetch;
		expect(await redeemPhoneLink('spent', { internalToken: 'internal-token', fetch })).toBeNull();
	});

	it('does not call the API without the internal token', async () => {
		const fetch = vi.fn() as unknown as typeof globalThis.fetch;
		expect(await redeemPhoneLink('once', { internalToken: '', fetch })).toBeNull();
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('reading the capture the phone may photograph', () => {
	it('keeps the cookie when the API cannot be reached', async () => {
		const fetch = vi.fn(async () => fail(503)) as unknown as typeof globalThis.fetch;
		expect(
			await loadPhoneCapture('idvphone_secret', { internalToken: 'internal-token', fetch })
		).toEqual({ ok: false, ended: false });
	});

	it('ends the session when the API no longer recognises it', async () => {
		const fetch = vi.fn(async () => fail(404)) as unknown as typeof globalThis.fetch;
		expect(
			await loadPhoneCapture('idvphone_secret', { internalToken: 'internal-token', fetch })
		).toEqual({ ok: false, ended: true });
	});
});
