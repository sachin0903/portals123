/**
 * Dashboard loader: the click must not wait on its API calls, and the rail reads the caller's own
 * onboarding and their review inbox rather than a broad work feed.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';

const locals = {
	user: { id: 'u_1' },
	accessToken: 'tok',
	permissions: ['documents:read', 'contacts:write'],
	role: 'adviser'
};

function json(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

describe('dashboard load', () => {
	it('streams the dash so the shell paints before the calls settle', async () => {
		const urls: string[] = [];
		const fetch = (async (input: RequestInfo | URL) => {
			urls.push(String(input));
			return json({ items: [], total: 0, facets: {}, clients: 0, accounts: 0, aum: 0 });
		}) as unknown as typeof globalThis.fetch;
		const result = (await load({
			locals,
			fetch,
			url: new URL('http://portal.test/'),
			depends: () => {}
		} as never)) as { overview: Promise<unknown>; dash: Promise<unknown> };
		expect(result.overview).toBeInstanceOf(Promise);
		expect(result.dash).toBeInstanceOf(Promise);
		await Promise.all([result.overview, result.dash]);
		expect(urls.some((u) => u.includes('/crm/overview'))).toBe(true);
		expect(urls.some((u) => u.includes('/crm/contacts'))).toBe(true);
		// The caller's OWN onboarding. `mine` is decided in the API, because the portal knows the
		// caller by their WorkOS id and the assignee by their internal user id.
		expect(urls.some((u) => u.includes('/applications') && u.includes('mine=true'))).toBe(true);
		// The reader's own notes, stored centrally; RLS admits the owner alone.
		expect(urls.some((u) => u.endsWith('/me/notes'))).toBe(true);
		// Requests used to feed this rail. They do not any more: the requests queue sits behind
		// `documents:show` and a request row could only be chased.
		expect(urls.some((u) => u.includes('/documents/requests'))).toBe(false);
		expect(urls.some((u) => u.includes('/tasks'))).toBe(false);
		expect(urls.every((u) => !u.includes('limit=200'))).toBe(true);
	});

	it('streams the overview on its own, so the KPI tiles do not wait for the book', async () => {
		// The hero figure is the page's largest paint. Held inside one `Promise.all` with the paged
		// book and the two queue reads, it landed when the SLOWEST of them did; on its own stream
		// it lands when `/crm/overview` does.
		let releaseBook!: () => void;
		const bookHeld = new Promise<void>((resolve) => (releaseBook = resolve));
		const fetch = (async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.includes('/crm/overview')) return json({ clients: 3, accounts: 4, aum: 7_800_000 });
			await bookHeld;
			return json({ items: [], total: 0, facets: {} });
		}) as unknown as typeof globalThis.fetch;
		const result = (await load({
			locals,
			fetch,
			url: new URL('http://portal.test/'),
			depends: () => {}
		} as never)) as { overview: Promise<{ aum: number } | null>; dash: Promise<unknown> };

		// The overview settles while the book is still held.
		await expect(result.overview).resolves.toMatchObject({ aum: 7_800_000 });
		let dashSettled = false;
		void result.dash.then(() => (dashSettled = true));
		await Promise.resolve();
		expect(dashSettled).toBe(false);

		releaseBook();
		await result.dash;
	});

	it('reads a failed overview as null, never a rejected stream', async () => {
		const fetch = (async (input: RequestInfo | URL) =>
			String(input).includes('/crm/overview')
				? json({}, 500)
				: json({ items: [], total: 0, facets: {} })) as unknown as typeof globalThis.fetch;
		const result = (await load({
			locals,
			fetch,
			url: new URL('http://portal.test/'),
			depends: () => {}
		} as never)) as { overview: Promise<unknown> };
		await expect(result.overview).resolves.toBeNull();
	});

	it('reads cash and growth on the contacts row, not from a second request', async () => {
		const urls: string[] = [];
		const fetch = (async (input: RequestInfo | URL) => {
			urls.push(String(input));
			if (String(input).includes('/crm/contacts')) {
				return json({
					items: [{ id: 7, cash: 10, growth: 1 }],
					total: 1,
					facets: {}
				});
			}
			return json({ items: [], total: 0, facets: {}, clients: 0, accounts: 0, aum: 0 });
		}) as unknown as typeof globalThis.fetch;
		const result = (await load({
			locals,
			fetch,
			url: new URL('http://portal.test/'),
			depends: () => {}
		} as never)) as { dash: Promise<unknown> };
		await result.dash;
		expect(urls.some((u) => u.includes('/crm/contacts/metrics'))).toBe(false);
	});

	it('does not fetch the hidden task surface even when unrelated permissions change', async () => {
		const urls: string[] = [];
		const fetch = (async (input: RequestInfo | URL) => {
			urls.push(String(input));
			return json({ items: [], total: 0, facets: {} });
		}) as unknown as typeof globalThis.fetch;
		const result = (await load({
			locals: { ...locals, permissions: ['contacts:write'] },
			fetch,
			url: new URL('http://portal.test/'),
			depends: () => {}
		} as never)) as { dash: Promise<unknown> };
		await result.dash;
		expect(urls.some((u) => u.includes('/tasks'))).toBe(false);
	});
});
