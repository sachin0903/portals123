/**
 * The book accounts listing loader.
 *
 * THE DEFECT this pins. The page mounts the SAME shared listing as the platform's own `/accounts`,
 * which searches and pages through the API — but this loader fetched `/crm/accounts` with no query
 * at all, so the component fell back to filtering whatever rows it had been handed. One table, two
 * behaviours: an operator searching a firm's book was searching a single batch and being told the
 * answer for the whole firm. "No account for Halloway" meant "not in this batch".
 *
 * A failed read still renders empty — the same trade the platform page makes, because the shared
 * listing has no error flag yet (noted in the loader).
 */
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';

const TENANT = { id: 4, uid: 't_9f2ab1', name: 'Caldwell Wealth' };
const locals = {
	user: { id: 'u-ops' },
	accessToken: 'tok',
	permissions: ['book:read'],
	role: null
};
const parent = async () => ({ tenant: TENANT });

const page = (items: unknown[], total = items.length) => ({ items, total, facets: {} });

function run(body: unknown, status = 200, search = '') {
	const calls: URL[] = [];
	const fetch = (async (url: string | URL) => {
		const u = new URL(String(url));
		calls.push(u);
		return new Response(JSON.stringify(body), { status });
	}) as unknown as typeof globalThis.fetch;
	const url = new URL(`http://ops.test/tenants/${TENANT.uid}/book/accounts${search}`);
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	return { calls, load: () => (load as any)({ locals, fetch, parent, url }) };
}

describe('the book accounts loader', () => {
	it('reads the tenant’s accounts through the book target', async () => {
		const { calls, load } = run(page([{ id: 1 }]));
		const result = await load();
		expect(calls[0].pathname).toBe('/crm/accounts');
		expect(calls[0].searchParams.get('tenant')).toBe(TENANT.uid);
		expect(result.accounts).toEqual([{ id: 1 }]);
	});

	it('sends the search to the API rather than filtering what came back', async () => {
		const { calls, load } = run(page([]), 200, '?q=halloway');
		await load();
		expect(calls[0].searchParams.get('q')).toBe('halloway');
	});

	it('forwards the filters the listing understands', async () => {
		const { calls, load } = run(page([]), 200, '?status=active&adviser=u-7&type=ISA&model=3');
		await load();
		const sent = calls[0].searchParams;
		expect(sent.get('status')).toBe('active');
		expect(sent.get('adviser')).toBe('u-7');
		expect(sent.get('type')).toBe('ISA');
		expect(sent.get('model')).toBe('3');
	});

	it('keeps the `?tenant=` target alongside the query', async () => {
		// Both live on one query string. Appending a second one is how a book call ends up with a
		// stray `?` and silently loses its tenant — which reads as an empty book, not as an error.
		const { calls, load } = run(page([]), 200, '?q=smith');
		await load();
		expect(calls[0].searchParams.get('tenant')).toBe(TENANT.uid);
		expect(calls[0].searchParams.get('q')).toBe('smith');
	});

	it('reports the match count across the firm, not the window', async () => {
		// What lets the listing say "25 of 380". Counting the rows in hand makes every book exactly
		// one page long, which reads as "that is all this firm has".
		const { load } = run(page([{ id: 1 }, { id: 2 }], 380));
		const result = await load();
		expect(result.accounts).toHaveLength(2);
		expect(result.matchCount).toBe(380);
	});

	it('collapses a failed read to an empty list (platform parity)', async () => {
		const result = await run({ detail: 'boom' }, 500).load();
		expect(result.accounts).toEqual([]);
		expect(result.matchCount).toBe(0);
	});
});
