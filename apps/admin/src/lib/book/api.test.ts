/** The book URL builder — the one rule that keeps the whole section honest: EVERY call carries
 *  `?tenant=<uid>`, alongside (never instead of) the endpoint's own params. */
import { describe, expect, it } from 'vitest';
import { bookUrl, getJson } from './api';

describe('bookUrl', () => {
	it('appends the tenant target to a bare path', () => {
		const u = new URL(bookUrl('/crm/contacts', 't_9f2ab1'));
		expect(u.pathname).toBe('/crm/contacts');
		expect(u.searchParams.get('tenant')).toBe('t_9f2ab1');
	});

	it('carries the endpoint’s own params alongside the target', () => {
		const u = new URL(bookUrl('/crm/holdings', 't_9f2ab1', { contact_id: '5' }));
		expect(u.searchParams.get('contact_id')).toBe('5');
		expect(u.searchParams.get('tenant')).toBe('t_9f2ab1');
	});

	it('URL-encodes what needs it', () => {
		const u = new URL(bookUrl('/tasks', 't_9f2ab1', { assignee: 'contact:5' }));
		expect(u.searchParams.get('assignee')).toBe('contact:5');
	});
});

describe('getJson', () => {
	it('distinguishes a failure from an empty result', async () => {
		const ok = (async () => new Response('[]')) as unknown as typeof globalThis.fetch;
		expect(await getJson(ok, 'http://api.test/x', {})).toEqual({ ok: true, data: [] });

		const bad = (async () => new Response('{}', { status: 500 })) as unknown as typeof fetch;
		expect(await getJson(bad, 'http://api.test/x', {})).toEqual({ ok: false, status: 500 });

		const down = (async () => {
			throw new Error('ECONNREFUSED');
		}) as unknown as typeof globalThis.fetch;
		expect(await getJson(down, 'http://api.test/x', {})).toEqual({ ok: false, status: 0 });
	});
});
