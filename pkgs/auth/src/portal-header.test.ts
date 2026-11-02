import { afterEach, describe, expect, it, vi } from 'vitest';
import { installPortalHeader, PORTAL_HEADER } from './portal-header';
import { mintPageTrace, withTraceParent } from './trace';

const original = globalThis.fetch;

afterEach(() => {
	globalThis.fetch = original;
});

describe('installPortalHeader', () => {
	it('names the portal on requests to the API and leaves every other request alone', async () => {
		const seen: { url: string; portal: string | null }[] = [];
		globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
			const headers = new Headers(init?.headers);
			seen.push({ url, portal: headers.get(PORTAL_HEADER) });
			return new Response('ok');
		}) as typeof fetch;

		installPortalHeader('operations', () => 'http://127.0.0.1:8000');

		await fetch('http://127.0.0.1:8000/me', { headers: { Authorization: 'Bearer t' } });
		await fetch('https://example.test/elsewhere');

		expect(seen).toEqual([
			{ url: 'http://127.0.0.1:8000/me', portal: 'operations' },
			{ url: 'https://example.test/elsewhere', portal: null }
		]);
	});

	it('keeps a portal header the caller already set', async () => {
		let portal: string | null = null;
		globalThis.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
			portal = new Headers(init?.headers).get(PORTAL_HEADER);
			return new Response('ok');
		}) as typeof fetch;

		installPortalHeader('operations', () => 'http://127.0.0.1:8000');
		await fetch('http://127.0.0.1:8000/me', { headers: { [PORTAL_HEADER]: 'platform' } });
		expect(portal).toBe('platform');
	});

	it('sets the page traceparent on API calls and keeps one the caller already set', async () => {
		const seen: (string | null)[] = [];
		globalThis.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
			seen.push(new Headers(init?.headers).get('traceparent'));
			return new Response('ok');
		}) as typeof fetch;

		installPortalHeader('platform', () => 'http://127.0.0.1:8000');
		const page = mintPageTrace();
		await withTraceParent(page.traceparent, () => fetch('http://127.0.0.1:8000/me'));
		await fetch('http://127.0.0.1:8000/me');
		await withTraceParent(page.traceparent, () =>
			fetch('http://127.0.0.1:8000/me/rum', {
				headers: { traceparent: '00-' + 'ab'.repeat(16) + '-' + 'cd'.repeat(8) + '-00' }
			})
		);

		expect(seen[0]).toBe(page.traceparent);
		expect(seen[1]).toBeNull();
		expect(seen[2]).toBe('00-' + 'ab'.repeat(16) + '-' + 'cd'.repeat(8) + '-00');
	});
});

describe('mintPageTrace', () => {
	it('is an unsampled W3C traceparent', () => {
		const page = mintPageTrace();
		expect(page.traceId).toMatch(/^[0-9a-f]{32}$/);
		expect(page.spanId).toMatch(/^[0-9a-f]{16}$/);
		expect(page.traceparent).toBe(`00-${page.traceId}-${page.spanId}-00`);
	});
});
