/**
 * The book clients loader: every read must carry `?tenant=<uid>` (without it the API resolves the
 * OPERATIONS tenant's own empty book and the page would quietly render nothing), and a failed
 * read must surface as a flag, never as an empty book.
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

const ROW = (id: number, value_as_of: string | null) => ({
	id,
	uid: `c_${id}`,
	type: 'individual',
	service_type: null,
	enabled: true,
	name: `Client ${id}`,
	title: null,
	first_name: null,
	surname: `Client ${id}`,
	email: null,
	mobile: null,
	account_count: 1,
	advisers: [],
	portfolio_value: 100,
	cash: 10,
	growth: 5,
	valued_account_count: 1,
	value_as_of
});

/** What the loader returns once the `redirect()` branch (typed `void`) is excluded. */
type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

function run(handler: (url: URL) => { body?: unknown; status?: number }, search = '') {
	const calls: URL[] = [];
	const fetch = (async (url: string | URL) => {
		const u = new URL(String(url));
		calls.push(u);
		const { body = [], status = 200 } = handler(u);
		return new Response(JSON.stringify(body), { status });
	}) as unknown as typeof globalThis.fetch;
	return {
		calls,
		load: async (): Promise<Loaded> =>
			(await load({
				locals,
				fetch,
				parent,
				url: new URL(`http://ops.test/tenants/${TENANT.uid}/book/clients${search}`)
			} as unknown as Parameters<typeof load>[0])) as Loaded
	};
}

describe('the book clients loader', () => {
	it('targets the tenant on the live book and stamps the newest as-at', async () => {
		const { calls, load } = run(() => ({
			body: [ROW(1, '2026-07-30'), ROW(2, '2026-08-01')]
		}));
		const result = await load();

		expect(calls).toHaveLength(1);
		expect(calls[0].pathname).toBe('/crm/contacts');
		expect(calls[0].searchParams.get('tenant')).toBe(TENANT.uid);
		expect(calls[0].searchParams.get('limit')).toBe('25');
		expect(result.clients).toHaveLength(2);
		expect(result.archived).toEqual([]);
		expect(result.valuesAsOf).toBe('2026-08-01');
		expect(result.loadError).toBe(false);
		expect(result.archivedError).toBe(false);
	});

	it('fetches the archive only when that segment is selected', async () => {
		const { calls, load } = run(
			(u) =>
				u.searchParams.get('archived') === 'true'
					? { body: [ROW(9, null)] }
					: { body: [ROW(1, '2026-07-30')] },
			'?segment=closed'
		);
		const result = await load();
		expect(calls).toHaveLength(2);
		for (const u of calls) {
			expect(u.searchParams.get('tenant')).toBe(TENANT.uid);
		}
		expect(calls.filter((u) => u.searchParams.get('archived') === 'true')).toHaveLength(1);
		expect(result.archived).toHaveLength(1);
	});

	it('reports a failed read as a flag — never as an empty book', async () => {
		const { load } = run(() => ({ status: 500 }));
		const result = await load();
		expect(result.clients).toEqual([]);
		expect(result.loadError).toBe(true);
		expect(result.archivedError).toBe(false);
	});

	it('reports the archive failing independently of the live book', async () => {
		const { load } = run(
			(u) =>
				u.searchParams.get('archived') === 'true' ? { status: 403 } : { body: [ROW(1, null)] },
			'?segment=closed'
		);
		const result = await load();
		expect(result.clients).toHaveLength(1);
		expect(result.loadError).toBe(false);
		expect(result.archivedError).toBe(true);
	});
});
