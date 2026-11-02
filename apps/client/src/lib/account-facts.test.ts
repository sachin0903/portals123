import { describe, expect, it } from 'vitest';
import type { Account, Holding } from '$lib/api';
import {
	accountFacts,
	accountHeaderActions,
	isOpenAccount,
	selfServeAccounts
} from './account-facts';

const NOW = new Date(2026, 9, 1);

const account = (over: Partial<Account> = {}): Account =>
	({
		id: 1,
		uid: 'acc000000001',
		name: 'Max Oakden GIA',
		status: 'active',
		currency: 'GBP',
		value: 452_728.25,
		contact_id: 92,
		risk_level: null,
		custody: null,
		opened_at: null,
		product_name: 'General Investment Account',
		product_type: 'GIA',
		cash: 12_000,
		growth: 31_250.5,
		...over
	}) as Account;

const holding = (
	asset_name: string,
	value: number,
	asset_type: string | null = 'Fund',
	isin: string | null = 'GB00TEST0001'
): Holding => ({ asset_name, value, asset_type, isin }) as Holding;

const holdings = [
	holding('Cash', 12_000, 'Cash', null),
	holding('Vanguard FTSE Global All Cap', 300_000),
	holding('iShares Core UK Gilts', 140_728.25)
];

describe('account money actions', () => {
	it('offers payment and withdrawal only on an execution-only account', () => {
		expect(accountHeaderActions('execution_only')).toBe('deposit_withdraw');
		expect(accountHeaderActions('advised')).toBe('none');
		expect(accountHeaderActions('discretionary')).toBe('none');
		expect(accountHeaderActions(null)).toBe('none');
	});

	it('keeps only the execution-only accounts when the book cannot move money', () => {
		const accounts = [
			{ id: 1, service_type: 'advised' },
			{ id: 2, service_type: 'execution_only' },
			{ id: 3, service_type: null }
		];
		expect(selfServeAccounts(accounts, false).map((a) => a.id)).toEqual([2]);
		expect(selfServeAccounts(accounts, true)).toEqual(accounts);
	});

	it('never offers a draft — an application in progress is not an account to pay into', () => {
		// The draft is what the transfer page showed as "Seccl GIA £0.00" for a client whose home
		// and accounts pages (the API's valued set) had never listed it.
		const accounts = [
			{ id: 1, status: 'active', service_type: 'execution_only' },
			{ id: 2, status: 'draft', service_type: null },
			{ id: 3, status: 'Draft ', service_type: 'execution_only' },
			{ id: 4, status: 'closed', service_type: 'execution_only' }
		];
		expect(selfServeAccounts(accounts, true).map((a) => a.id)).toEqual([1, 4]);
		expect(selfServeAccounts(accounts, false).map((a) => a.id)).toEqual([1, 4]);
		expect(isOpenAccount({ status: undefined })).toBe(true);
	});
});

describe('accountFacts', () => {
	it('gives every account the same three facts — never a lone Cash tile', () => {
		const labels = accountFacts(account(), holdings, NOW).map((f) => f.label);
		expect(labels).toEqual(['Cash', 'Invested', 'Gain / loss']);
	});

	it('states cash, what is invested and the gain from the account’s own figures', () => {
		const [cash, invested, gain] = accountFacts(account(), holdings, NOW);
		expect(cash).toEqual({ label: 'Cash', value: '£12,000.00', sub: undefined });
		// Value less cash — and the count is of investments, so the cash row is not "3 holdings".
		expect(invested).toEqual({ label: 'Invested', value: '£440,728.25', sub: '2 holdings' });
		expect(gain).toEqual({
			label: 'Gain / loss',
			value: '+£31,250.50',
			sub: 'On what you hold today',
			valueTone: 'positive'
		});
	});

	it('tones a loss and says nothing when the lake has no gain figure', () => {
		expect(accountFacts(account({ growth: -500 }), holdings, NOW)[2]).toMatchObject({
			value: '-£500.00',
			valueTone: 'negative'
		});
		expect(accountFacts(account({ growth: null }), holdings, NOW)[2]).toEqual({
			label: 'Gain / loss',
			value: '—',
			sub: undefined,
			valueTone: undefined
		});
		expect(accountFacts(account({ growth: 0 }), holdings, NOW)[2].valueTone).toBeUndefined();
	});

	it('says nothing for a gain that exceeds the valuation — a cost of nothing is a bad basis', () => {
		// The dev extract that produced "+£1,580,684.45" on a £452,728.25 account.
		expect(accountFacts(account({ growth: 1_580_684.45 }), holdings, NOW)[2].value).toBe('—');
		expect(accountFacts(account({ growth: 452_728.25 }), holdings, NOW)[2].value).toBe('—');
		// Just under is a real (if remarkable) gain; with no valuation there is nothing to test against.
		expect(accountFacts(account({ growth: 452_000 }), holdings, NOW)[2].value).toBe('+£452,000.00');
		expect(accountFacts(account({ value: null, growth: 9e9 }), holdings, NOW)[2].value).toBe(
			'+£9,000,000,000.00'
		);
	});

	it('falls back to the holdings when the lake has not valued the account', () => {
		const [cash, invested] = accountFacts(account({ value: null, cash: null }), holdings, NOW);
		expect(cash.value).toBe('£12,000.00');
		expect(invested.value).toBe('£440,728.25');
		expect(accountFacts(account(), [], NOW)[1]).toEqual({
			label: 'Invested',
			value: '£440,728.25',
			sub: undefined
		});
	});

	it('adds the ISA allowance — the custodian’s figure, or the statutory limit when silent', () => {
		expect(
			accountFacts(
				account({ product_type: 'ISA', allowance_used: 5000, allowance_limit: 20_000 }),
				holdings,
				NOW
			)[3]
		).toEqual({
			label: 'ISA allowance used',
			value: '£5,000.00',
			sub: '£15,000.00 left'
		});
		expect(accountFacts(account({ product_type: 'JISA' }), holdings, NOW)[3]).toEqual({
			label: 'JISA allowance',
			value: '£9,000.00',
			sub: 'Annual limit for 2026/27 · amount used not yet reported'
		});
	});

	it('adds a pension’s split and the administrator’s cash', () => {
		const facts = accountFacts(
			account({
				product_type: 'SIPP',
				provider_positions: [
					{
						source: 'morgan-lloyd',
						cash: 800,
						as_of: null,
						crystallised: 100_000,
						uncrystallised: 350_000
					}
				]
			}),
			holdings,
			NOW
		);
		expect(facts.map((f) => f.label)).toEqual([
			'Cash',
			'Invested',
			'Gain / loss',
			'Crystallised',
			'Uncrystallised'
		]);
		expect(facts[0]).toMatchObject({
			value: '£12,800.00',
			sub: '£12,000.00 custodian · £800.00 Morgan Lloyd'
		});
		expect(facts[3].value).toBe('£100,000.00');
	});
});
