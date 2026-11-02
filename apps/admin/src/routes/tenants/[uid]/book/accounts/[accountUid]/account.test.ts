/**
 * The book account-detail loaders: `?tenant=` on every read, 404/403 propagated as page errors,
 * the platform's holdings mapping reproduced (gainPct re-derived from growth/cost, absurd bases
 * dropped), and the transactions the TARGET FIRM'S rather than a shared fixture corpus.
 *
 * The layout is the account itself. Holdings and the ledger belong to the page, matching the
 * platform record: a filter change must not re-read the lake.
 *
 * That last one is the case worth naming: this loader served `transactionFixtures` filtered by
 * `account_id`, so an operator saw the same sixteen invented rows whichever firm's account they
 * opened — and invented rows read exactly like a real ledger. The `?tenant=` assertion below is
 * what keeps them the firm's.
 */
import { describe, expect, it } from 'vitest';
import { load as layoutLoad } from './+layout.server';
import { load } from './+page.server';

const TENANT = { id: 4, uid: 't_9f2ab1', name: 'Caldwell Wealth' };
const locals = {
	user: { id: 'u-ops' },
	accessToken: 'tok',
	permissions: ['book:read'],
	role: null
};
const parent = async () => ({ tenant: TENANT });

const ACCOUNT = {
	id: 2,
	uid: 'a_2',
	name: 'General Investment Account',
	status: 'active',
	currency: 'GBP',
	value: 1000,
	contact_id: 5,
	risk_level: null,
	custody: null,
	opened_at: null,
	closed_at: null,
	product_id: null,
	product_name: null,
	product_type: 'gia',
	discretionary: false,
	model_id: null,
	model_name: null,
	adviser: null,
	platform_identifier: null,
	platform_name: null,
	fee_packages: [],
	third_party_pension_provider: null,
	fees: null,
	payments: null,
	withdrawals: null,
	transfer: null,
	bank_accounts: null,
	cash: 100,
	growth: 50,
	client: {
		id: 5,
		uid: 'c_5',
		type: 'individual',
		name: 'Theo Caldwell',
		title: null,
		first_name: 'Theo',
		surname: 'Caldwell',
		bank_account: null
	}
};

const HOLDINGS = [
	// growth 10 on cost 100 ⇒ 10%.
	{
		asset_name: 'Global Equity Fund',
		isin: 'IE00X',
		quantity: 4,
		price: 25,
		value: 110,
		book_cost: 100,
		growth: 10
	},
	// growth/cost > 10x (the lake's percent-as-fraction confusion) ⇒ dropped to 0, not +6,303%.
	{ asset_name: 'Odd Row', quantity: 1, price: 1, value: 1, book_cost: 0.01, growth: 63.03 }
];

/** One movement, as `/crm/transactions?account_id=` returns it. */
const TXNS = [
	{
		uid: 'seccl:t1',
		date: '2026-08-01',
		account_id: 2,
		account_name: 'General Investment Account',
		account_uid: 'a_2',
		client_id: 5,
		client_name: 'Theo Caldwell',
		adviser: null,
		type: 'deposit',
		subtype: 'Cash Receipt',
		method: null,
		amount: 500,
		asset: null,
		quantity: null,
		status: 'settled',
		product_type: 'gia'
	}
];

function runLayout(accountStatus?: number) {
	const calls: URL[] = [];
	const fetch = (async (url: string | URL) => {
		const u = new URL(String(url));
		calls.push(u);
		if (u.pathname === '/crm/accounts/acc_2f8b') {
			return new Response(JSON.stringify(accountStatus ? { detail: 'no' } : ACCOUNT), {
				status: accountStatus ?? 200
			});
		}
		if (u.pathname === '/crm/holdings' || u.pathname === '/crm/transactions') {
			throw new Error(`layout must not fetch ${u.pathname}`);
		}
		throw new Error(`unexpected fetch: ${u}`);
	}) as unknown as typeof globalThis.fetch;
	return {
		calls,
		load: () =>
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			(layoutLoad as any)({
				locals,
				params: { uid: TENANT.uid, accountUid: 'acc_2f8b' },
				fetch,
				parent
			})
	};
}

function runPage(overrides: Partial<Record<'holdings' | 'transactions', number>> = {}) {
	const calls: URL[] = [];
	const fetch = (async (url: string | URL) => {
		const u = new URL(String(url));
		calls.push(u);
		const respond = (body: unknown, fail?: number) =>
			new Response(JSON.stringify(fail ? { detail: 'no' } : body), { status: fail ?? 200 });
		if (u.pathname === '/crm/holdings') return respond(HOLDINGS, overrides.holdings);
		if (u.pathname === '/crm/transactions') return respond(TXNS, overrides.transactions);
		throw new Error(`unexpected fetch: ${u}`);
	}) as unknown as typeof globalThis.fetch;
	return {
		calls,
		load: () =>
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			(load as any)({
				locals,
				params: { uid: TENANT.uid, accountUid: 'acc_2f8b' },
				fetch,
				parent: async () => ({ tenant: TENANT, account: ACCOUNT })
			})
	};
}

describe('the book account layout', () => {
	it('loads the account for the target tenant and nothing else', async () => {
		const { calls, load } = runLayout();
		const result = await load();
		expect(result.account).toEqual(ACCOUNT);
		expect(calls).toHaveLength(1);
		expect(calls[0].pathname).toBe('/crm/accounts/acc_2f8b');
		expect(calls[0].searchParams.get('tenant')).toBe(TENANT.uid);
		expect(calls.some((u) => u.pathname === '/crm/holdings')).toBe(false);
		expect(calls.some((u) => u.pathname.includes('instructions'))).toBe(false);
		expect(calls.some((u) => u.pathname.includes('contributions'))).toBe(false);
	});

	it('propagates a 404 as a page error', async () => {
		await expect(runLayout(404).load()).rejects.toMatchObject({ status: 404 });
	});

	it('propagates a 403 rather than rendering a blank record', async () => {
		await expect(runLayout(403).load()).rejects.toMatchObject({ status: 403 });
	});
});

describe('the book account page', () => {
	it('targets the tenant on every read, narrowing holdings and transactions to the account', async () => {
		const { calls, load } = runPage();
		await load();
		expect(calls).toHaveLength(2);
		// Without `?tenant=` the API resolves the OPERATIONS tenant's own empty book, and the page
		// renders nothing for a firm that has a full ledger.
		for (const u of calls) expect(u.searchParams.get('tenant')).toBe(TENANT.uid);
		const narrowed = calls.filter((u) => u.searchParams.get('account_id') === '2');
		expect(narrowed.map((u) => u.pathname).sort()).toEqual(['/crm/holdings', '/crm/transactions']);
	});

	it('maps lake holdings the way the platform does — gainPct from growth/cost, absurd bases dropped', async () => {
		const result = await runPage().load();
		expect(result.holdings[0]).toMatchObject({
			name: 'Global Equity Fund',
			ticker: 'IE00X',
			wrapper: 'General Investment Account',
			gainPct: 0.1
		});
		expect(result.holdings[1].gainPct).toBeNull();
	});

	it('serves the firm’s own transactions, not a shared fixture corpus', async () => {
		const result = await runPage().load();
		expect(result.transactions).toEqual(TXNS);
		expect(result.loadError).toBe(false);
	});

	it('flags a failed transactions read rather than rendering an empty band', async () => {
		const result = await runPage({ transactions: 502 }).load();
		expect(result.transactions).toEqual([]);
		expect(result.loadError).toBe(true);
	});
});
