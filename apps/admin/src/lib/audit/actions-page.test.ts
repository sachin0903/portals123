/**
 * The trail "Load more" fetch wrapper, and the tripwire that keeps it the only copy of the recipe.
 *
 * The behaviour worth a test is the FAILURE contract: every caller maps `!res.ok` to `null` and keeps
 * its "Load more" button, so a failure must answer NON-2xx. A 200 `{events: [], hasMore: false}` is
 * indistinguishable from "you have reached the end of the trail" — the reader is told the whole
 * compliance record is loaded when a request merely broke. That is what each of the three admin
 * proxies used to own a copy of, which is why the source scan below exists.
 */
import { describe, expect, it, vi } from 'vitest';
import { globSync, readFileSync } from 'node:fs';
import { fetchActionsPage } from './actions-page';

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const session = { user: { id: 'ops_1' }, accessToken: 'token' };
const page = (query = 'limit=50') => new URLSearchParams(query);

/** A `fetch` double that records what it was called with. */
function recorder(body: unknown, status = 200) {
	const calls: { url: string; init?: RequestInit }[] = [];
	const fetch = vi.fn(async (url: string | URL, init?: RequestInit) => {
		calls.push({ url: String(url), init });
		return json(body, status);
	});
	return { calls, fetch: fetch as unknown as typeof globalThis.fetch };
}

describe('fetchActionsPage — the failure contract', () => {
	it('refuses a caller with no session at 401, not an empty page', async () => {
		const rec = recorder({ items: [] });
		const res = await fetchActionsPage(rec.fetch, '/admin/audit/actions', page(), {
			user: null,
			accessToken: null
		});
		expect(res.status).toBe(401);
		// And it never reaches the API — there is no token to reach it with.
		expect(rec.calls).toHaveLength(0);
	});

	it('turns a non-2xx from the API into a 502, never a swallowed 200', async () => {
		const res = await fetchActionsPage(
			recorder({ detail: 'boom' }, 500).fetch,
			'/admin/audit/actions',
			page(),
			session
		);
		expect(res.status).toBe(502);
	});

	it('turns a 403 into a 502 too — a lost permission is a failure to report, not a last page', async () => {
		const res = await fetchActionsPage(
			recorder({ detail: 'Not authorised' }, 403).fetch,
			'/admin/audit/actions',
			page(),
			session
		);
		expect(res.ok).toBe(false);
	});

	it('turns an unreachable API into a 502', async () => {
		const fetch = vi.fn(async () => {
			throw new Error('ECONNREFUSED');
		}) as unknown as typeof globalThis.fetch;
		const res = await fetchActionsPage(fetch, '/admin/audit/actions', page(), session);
		expect(res.status).toBe(502);
	});

	it('carries the same body shape on a failure, so a caller that ignores the status sees an empty page', async () => {
		const res = await fetchActionsPage(
			recorder({}, 500).fetch,
			'/admin/audit/actions',
			page(),
			session
		);
		expect(await res.json()).toEqual({ events: [], hasMore: false });
	});
});

describe('fetchActionsPage — the page it answers with', () => {
	it("maps {items, has_more} onto the caller's {events, hasMore}", async () => {
		const res = await fetchActionsPage(
			recorder({ items: [{ id: 9 }], has_more: true }).fetch,
			'/admin/audit/actions',
			page(),
			session
		);
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual({ events: [{ id: 9 }], hasMore: true });
	});

	it("trusts the API's has_more where it carries one, even on a full page", async () => {
		const items = Array.from({ length: 50 }, (_, i) => ({ id: i }));
		const res = await fetchActionsPage(
			recorder({ items, has_more: false }).fetch,
			'/admin/audit/actions',
			page('limit=50'),
			session
		);
		expect(((await res.json()) as { hasMore: boolean }).hasMore).toBe(false);
	});

	it('falls back to "a full page means more behind it" where the endpoint carries no has_more', async () => {
		// `GET /admin/audit` — the DML record-change trail — returns `{items, tenants}` and no flag.
		const items = Array.from({ length: 100 }, (_, i) => ({ id: i }));
		const res = await fetchActionsPage(
			recorder({ items, tenants: [] }).fetch,
			'/admin/audit',
			page('limit=100'),
			session
		);
		expect(((await res.json()) as { hasMore: boolean }).hasMore).toBe(true);
	});

	it('reports a short page as the last one under that fallback', async () => {
		const res = await fetchActionsPage(
			recorder({ items: [{ id: 1 }] }).fetch,
			'/admin/audit',
			page('limit=100'),
			session
		);
		expect(((await res.json()) as { hasMore: boolean }).hasMore).toBe(false);
	});

	it('tolerates a response with no items at all', async () => {
		const res = await fetchActionsPage(recorder({}).fetch, '/admin/audit', page(), session);
		expect(await res.json()).toEqual({ events: [], hasMore: false });
	});
});

describe('fetchActionsPage — what it sends', () => {
	it('calls the API path with the query it was given and a bearer token', async () => {
		const rec = recorder({ items: [], has_more: false });
		await fetchActionsPage(
			rec.fetch,
			'/admin/audit/actions',
			page('limit=50&before=900&tenant=ten_1'),
			session
		);
		const { url, init } = rec.calls[0];
		expect(url).toContain('/admin/audit/actions?');
		const sent = new URL(url).searchParams;
		expect(sent.get('limit')).toBe('50');
		expect(sent.get('before')).toBe('900');
		expect(sent.get('tenant')).toBe('ten_1');
		expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer token');
	});
});

describe('every admin "Load more" proxy goes through the wrapper', () => {
	/** The proxies. `globSync` from the app root — cwd for this suite. */
	const proxies = globSync('src/routes/**/more/+server.ts');

	/** Comments quote the very shapes this scan forbids (that is their job — the rule is subtle), so
	 *  match against the CODE alone. The same false positive `switch-columns.test.ts` records hitting. */
	const codeOf = (file: string) =>
		readFileSync(file, 'utf8')
			.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/(^|\s)\/\/.*$/gm, '');

	it('finds all four of them', () => {
		expect(proxies).toHaveLength(4);
	});

	it.each(proxies)('%s delegates the call and the failure contract', (file) => {
		const code = codeOf(file);
		expect(code).toContain("from '$lib/audit/actions-page'");
		expect(code).toContain('fetchActionsPage(');
		// Hand-rolling the call is how the failure contract drifted in the first place: the recipe is
		// six lines, four of which are the difference between "no more events" and "the fetch broke".
		expect(code).not.toContain('apiUrl(');
		expect(code).not.toContain('authHeaders(');
		expect(code).not.toContain('hasMore');
		expect(code).not.toMatch(/status:\s*(401|502)/);
	});
});
