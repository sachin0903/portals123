/**
 * The book transactions loader.
 *
 * This page rendered `@graphene/ui`'s fixture corpus, under a comment claiming it did so "exactly
 * as the platform's /transactions page". The platform page moved to the real feed and this one did
 * not follow, so every firm in the ops book showed the SAME sixteen invented rows — and an
 * operator cannot tell an invented ledger from a real one, which makes that worse than a blank
 * page. These cases pin the two things that keep it real: the `?tenant=` target (without it the
 * API answers for the operations tenant's own empty book) and the paged query round-trip.
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

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const page = (items: unknown[], total = items.length) => json({ items, total, facets: {} });

const ROW = {
	uid: 'seccl:t1',
	date: '2026-08-01',
	account_id: 2,
	account_name: 'General Investment Account',
	client_id: 5,
	client_name: 'Theo Caldwell',
	type: 'deposit',
	amount: 500,
	status: 'settled',
	product_type: 'gia'
};

function run(
	respond: (url: URL) => Response = () => page([ROW]),
	search = '',
	user: unknown = locals.user
) {
	const calls: URL[] = [];
	const fetch = (async (url: string | URL) => {
		const u = new URL(String(url));
		calls.push(u);
		return respond(u);
	}) as unknown as typeof globalThis.fetch;
	const event = {
		locals: { ...locals, user },
		fetch,
		url: new URL(`http://test/tenants/${TENANT.uid}/book/transactions${search}`),
		parent
	};
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	return { calls, load: () => (load as any)(event) };
}

describe('the book transactions loader', () => {
	it('reads the real listing, scoped to the target firm', async () => {
		const { calls, load } = run();
		const result = await load();
		expect(result.transactions).toEqual([ROW]);
		expect(result.matchCount).toBe(1);
		expect(calls).toHaveLength(1);
		expect(calls[0].pathname).toBe('/crm/transactions');
		expect(calls[0].searchParams.get('tenant')).toBe(TENANT.uid);
	});

	it('asks for the paged envelope, so the pager has a real total', async () => {
		const { calls, load } = run();
		await load();
		expect(calls[0].searchParams.get('paged')).toBe('true');
		expect(calls[0].searchParams.get('limit')).toBe('25');
	});

	it('forwards the URL search, filters and page to the API', async () => {
		// Filtering the page in hand would report "no transaction for Halloway" when it means
		// "not on this page" — the same reason the platform page round-trips.
		const { calls, load } = run(() => page([]), '?q=caldwell&type=deposit&offset=50');
		await load();
		expect(calls[0].searchParams.get('q')).toBe('caldwell');
		expect(calls[0].searchParams.get('type')).toBe('deposit');
		expect(calls[0].searchParams.get('offset')).toBe('50');
	});

	it('fails soft to an empty listing and FLAGS it', async () => {
		// "This firm has no transactions" is a real answer, so a failed read must not be able to
		// impersonate it — the page renders a retry banner off this flag.
		const result = await run(() => json({ detail: 'boom' }, 502)).load();
		expect(result.transactions).toEqual([]);
		expect(result.loadError).toBe(true);
	});

	it('bounces a signed-out visitor to /login', async () => {
		await expect(
			Promise.resolve().then(() => run(() => page([]), '', null).load())
		).rejects.toMatchObject({
			status: 303,
			location: '/login'
		});
	});
});
