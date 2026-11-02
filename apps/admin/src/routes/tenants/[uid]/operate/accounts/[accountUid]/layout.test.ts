/**
 * The operate-as-firm account layout: Drawdown is an administered pension, not the wrapper.
 *
 * Mirrors `apps/platform/src/routes/accounts/accounts.test.ts`. Operate used to set
 * `canDrawdown` from `isPensionProduct(product_type)` alone, so every SIPP showed Drawdown —
 * including one Morgan Lloyd has never heard of. `canContribute` stays on any pension: only the
 * drawdown journey belongs to the administrator. It now gates the Transactions-tab note, not a
 * Contribute button.
 *
 * The layout loads the account (and the model picker catalogue) only. Holdings, instructions and
 * fee packages belong to Overview, so a ledger filter cannot re-read them.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+layout.server';
import { load as overviewLoad } from './+page.server';
import { load as holdingsLoad } from './holdings/+page.server';
import { load as transactionsLoad } from './transactions/+page.server';

const tenant = { id: 4, uid: 'ten_acme', name: 'Acme Wealth' };

const ACCOUNT = {
	id: 2,
	uid: 'acc_sipp',
	name: 'Oakden SIPP',
	status: 'active',
	product_type: 'SIPP',
	is_pension: true,
	custodian: 'sei',
	scheme_administrator: 'morgan-lloyd',
	product_id: 1,
	model_id: null,
	currency: 'GBP',
	client: {
		id: 4,
		uid: 'cli_oakden',
		type: 'individual',
		first_name: 'Max',
		surname: 'Oakden'
	}
};

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

const run = async (account: Record<string, unknown> = ACCOUNT, writing = true) => {
	const urls: string[] = [];
	const parent = async () => ({ tenant, writing });
	// SvelteKit types a load's return as possibly `void`; this loader always returns data or
	// throws, so narrow once here — same pin as the platform account layout tests.
	const result = (await load({
		locals: { user: { id: 'ops' }, accessToken: 't' },
		params: { accountUid: String(account.uid ?? ACCOUNT.uid) },
		fetch: async (url: string | URL | Request) => {
			const u = String(url);
			urls.push(u);
			if (u.includes('/crm/holdings')) throw new Error('layout must not fetch holdings');
			if (u.includes('/instructions')) throw new Error('layout must not fetch instructions');
			if (u.includes('/catalog/fee-packages')) {
				throw new Error('layout must not fetch fee packages');
			}
			if (u.includes('/contributions')) throw new Error('layout must not fetch contributions');
			if (u.includes('/crm/models')) return json([]);
			if (u.includes('/reviews/policies')) {
				return json([
					{
						action_key: 'client.bank',
						mode: 'four_eye',
						require_justification: true
					}
				]);
			}
			if (u.includes('/firm/money-actions')) {
				return json({ disabled: [], require_justification: ['transaction.payment'] });
			}
			return json(account);
		},
		parent
	} as never)) as Exclude<Awaited<ReturnType<typeof load>>, void>;
	return { result, urls };
};

describe('the operate account layout', () => {
	it('loads the account and not the extras', async () => {
		const { result, urls } = await run();
		expect(result.account.uid).toBe(ACCOUNT.uid);
		expect(result.holdings).toEqual([]);
		expect(result.justificationRequired).toEqual(['client.bank', 'transaction.payment']);
		expect(urls.some((u) => u.includes('/crm/accounts/acc_sipp'))).toBe(true);
		expect(urls.some((u) => u.includes('/crm/holdings'))).toBe(false);
		expect(urls.some((u) => u.includes('/instructions'))).toBe(false);
		expect(urls.some((u) => u.includes('/contributions'))).toBe(false);
	});

	it('gates drawdown on a pension Morgan Lloyd administers, not the wrapper', async () => {
		expect((await run()).result.canDrawdown).toBe(true);
		expect((await run()).result.canContribute).toBe(true);

		const gia = (await run({ ...ACCOUNT, is_pension: false, product_type: 'GIA' })).result;
		expect(gia.canDrawdown).toBe(false);
		expect(gia.canContribute).toBe(false);

		// THE CASE THE OLD GATE GOT WRONG. A real SIPP, not administered here, offered Drawdown.
		const elsewhere = (await run({ ...ACCOUNT, scheme_administrator: null })).result;
		expect(elsewhere.canDrawdown).toBe(false);
		expect(elsewhere.canContribute).toBe(true);

		const secclSipp = (await run({ ...ACCOUNT, custodian: 'seccl' })).result;
		expect(secclSipp.canDrawdown).toBe(false);
		expect(secclSipp.canContribute).toBe(true);
	});

	it('reads the shelf first and falls back to the wrapper set', async () => {
		const shelfSaysNo = (await run({ ...ACCOUNT, is_pension: false })).result;
		expect(shelfSaysNo.canDrawdown).toBe(false);
		expect(shelfSaysNo.canContribute).toBe(false);

		const noColumn = { ...ACCOUNT } as Record<string, unknown>;
		delete noColumn.is_pension;
		const fallback = (await run(noColumn)).result;
		expect(fallback.canDrawdown).toBe(true);
		expect(fallback.canContribute).toBe(true);
	});

	it('hides both money actions when the portal is read-only', async () => {
		const { result } = await run(ACCOUNT, false);
		expect(result.canDrawdown).toBe(false);
		expect(result.canContribute).toBe(false);
	});
});

describe('the operate account overview', () => {
	it('asks for this account’s holdings and account-scoped instructions', async () => {
		const urls: string[] = [];
		await overviewLoad({
			locals: { user: { id: 'ops' }, accessToken: 't' },
			parent: async () => ({ tenant, account: ACCOUNT, writing: true }),
			fetch: async (url: string | URL | Request) => {
				const u = String(url);
				urls.push(u);
				if (u.includes('/crm/holdings')) return json([]);
				if (u.includes('/instructions')) return json([]);
				if (u.includes('/catalog/fee-packages')) return json([]);
				throw new Error(`unexpected overview fetch: ${u}`);
			}
		} as never);
		expect(urls.some((u) => u.includes('/crm/holdings') && u.includes('account_id=2'))).toBe(true);
		expect(
			urls.some(
				(u) => u.includes('/firm/contacts/cli_oakden/instructions') && u.includes('account_id=2')
			)
		).toBe(true);
	});
});

describe('the operate account holdings loader', () => {
	it('loads holdings and nothing else', async () => {
		const urls: string[] = [];
		const result = (await holdingsLoad({
			locals: { user: { id: 'ops' }, accessToken: 't' },
			parent: async () => ({ tenant, account: ACCOUNT }),
			fetch: async (url: string | URL | Request) => {
				const u = String(url);
				urls.push(u);
				if (u.includes('/crm/holdings')) {
					return json([
						{
							asset_name: 'Fund',
							book_cost: 100,
							growth: 10,
							quantity: 1,
							price: 110,
							value: 110
						}
					]);
				}
				throw new Error(`unexpected holdings fetch: ${u}`);
			}
		} as never)) as Exclude<Awaited<ReturnType<typeof holdingsLoad>>, void>;
		expect(result.holdings).toHaveLength(1);
		expect(result.holdings[0].gainPct).toBe(0.1);
		expect(urls).toHaveLength(1);
		expect(urls[0]).toContain('/crm/holdings');
	});
});

describe('the operate account transactions loader', () => {
	it('does not re-fetch holdings when the ledger is filtered', async () => {
		const urls: string[] = [];
		await transactionsLoad({
			locals: { user: { id: 'ops' }, accessToken: 't' },
			parent: async () => ({ tenant, account: ACCOUNT }),
			url: new URL(
				'http://test/tenants/ten_acme/operate/accounts/acc_sipp/transactions?date_from=2026-01-01'
			),
			fetch: async (url: string | URL | Request) => {
				const u = String(url);
				urls.push(u);
				if (u.includes('/crm/holdings')) throw new Error('transactions must not fetch holdings');
				if (u.includes('/crm/transactions')) return json([]);
				throw new Error(`unexpected transactions fetch: ${u}`);
			}
		} as never);
		expect(urls).toHaveLength(1);
		expect(urls[0]).toContain('/crm/transactions');
		expect(urls[0]).toContain('date_from=2026-01-01');
	});
});
