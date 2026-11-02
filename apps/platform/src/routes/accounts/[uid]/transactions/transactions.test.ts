/**
 * Transactions for an account record are loaded on the band route, not the layout.
 */
import { describe, expect, it } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { load } from './+page.server';

type Event = Parameters<typeof load>[0];
/** What the loader returns once the `redirect()` branch (typed `void`) is excluded. */
type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;
const run = async (event: Event) => {
	const loaded = (await load(event)) as Loaded;
	return { ...loaded, ...(await loaded.band) };
};

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

const TXNS = [
	{
		uid: 'seccl:t1',
		date: '2026-08-01',
		account_id: 2,
		type: 'deposit',
		amount: 2500,
		status: 'settled'
	}
];

describe('the /accounts/[uid]/transactions loader', () => {
	let requested = '';
	const event = (transactions: unknown[] | number = TXNS, search = '') =>
		({
			parent: async () => ({ account: { id: 2 }, loadError: false }),
			locals: { accessToken: 't' },
			url: new URL(`http://test/accounts/acc_7f3a/transactions${search}`),
			fetch: async (url: string | URL | Request) => {
				const u = String(url);
				if (u.includes('/crm/holdings')) throw new Error('transactions must not fetch holdings');
				requested = u;
				return typeof transactions === 'number'
					? json({ detail: 'boom' }, transactions)
					: json(transactions);
			}
		}) as unknown as Event;

	it('asks the API to narrow movements to this account', async () => {
		const result = await run(event());
		expect(result.transactions).toEqual(TXNS);
		expect(requested).toContain('account_id=2');
		expect(requested).toContain(`limit=${PAGE_SIZE}`);
		expect(requested).toContain('paged=true');
		expect(result.transactionTotal).toBe(TXNS.length);
		expect(result.loadError).toBe(false);
	});

	it('carries a holding ISIN into the transaction read', async () => {
		await run(event(TXNS, '?isin=GB00TEST'));
		expect(requested).toContain('isin=GB00TEST');
	});

	it('flags a failed transactions read instead of an empty band', async () => {
		const result = await run(event(502));
		expect(result.transactions).toEqual([]);
		expect(result.loadError).toBe(true);
	});

	it('does not re-fetch holdings when the ledger is filtered', async () => {
		await run(event(TXNS, '?date_from=2026-01-01&date_to=2026-01-31'));
		expect(requested).toContain('/crm/transactions');
		expect(requested).toContain('date_from=2026-01-01');
		expect(requested).not.toContain('/crm/holdings');
	});
});
