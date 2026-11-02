// SSR coverage for the shared account-book surfaces.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { createRawSnippet } from 'svelte';
import AccountsListing from './accounts-listing.svelte';
import AccountDetail from './account-detail.svelte';
import TransactionsListing from './transactions-listing.svelte';
import { emptyQuery } from '../listing/query.js';
import { formatDate } from '../../../dates.js';
import { transactionFixtures } from './transaction-fixtures.js';
import { needsAction, type Transaction } from './transactions.js';
import type { Account, AccountBookLinks, AccountClient, AccountDetailData } from './types.js';
import type { ReviewRow } from '../reviews/types.js';
import { LINK_RECORD } from '../../../text-shell.js';

// Deliberately NOT the platform's routes — the assertions below prove the components render
// whatever the app injects rather than remembering where they were extracted from.
// The builders take a UID: every injected href is built from the record's public address, not
// from the serial beside it (docs/api.md → Addressing).
const links: AccountBookLinks = {
	account: (uid) => `/x/accounts/${uid}`,
	client: (uid) => `/x/clients/${uid}`
};

const client = (over: Partial<AccountClient> = {}): AccountClient => ({
	id: 4,
	uid: 'con_max',
	type: 'individual',
	name: null,
	title: null,
	first_name: 'Max',
	surname: 'Oakden',
	...over
});

const account = (over: Partial<Account> = {}): Account => ({
	id: 2,
	uid: 'seccl_AXF5AC13E',
	name: 'Oakden SIPP',
	status: 'active',
	currency: 'GBP',
	value: 250_000,
	contact_id: 4,
	risk_level: 'Balanced',
	custody: 'platform',
	opened_at: '2024-03-01',
	product_name: 'Personal Pension',
	product_type: 'SIPP',
	discretionary: true,
	service_type: 'discretionary',
	model_id: 7,
	model_name: 'Growth 60',
	adviser: 'Mark Alford',
	client: client(),
	cash: 12_000,
	growth: 31_000,
	...over
});

const detail = (over: Partial<AccountDetailData> = {}): AccountDetailData => {
	const { client: c, ...base } = account();
	return {
		...base,
		closed_at: null,
		product_id: 12,
		platform_identifier: 'AXF5AC13E',
		platform_name: 'Seccl',
		fee_packages: [
			{
				id: 3,
				name: 'Advised standard',
				sort_order: 0,
				fee_type: 'percentage',
				rule: { rate: 0.75 },
				frequency: 'ongoing',
				charge_basis: 'aum',
				currency: 'GBP',
				vatable: true,
				minimum_amount: 250,
				maximum_amount: null,
				payer: null,
				description: null
			},
			{
				id: 8,
				name: 'Platform charge',
				sort_order: 1,
				fee_type: 'tiered',
				rule: {
					tiers: [
						{ from: 0, to: 250000, rate: 0.3 },
						{ from: 250000, to: null, rate: 0.2 }
					]
				},
				frequency: 'ongoing',
				charge_basis: 'aum',
				currency: 'GBP',
				vatable: false,
				minimum_amount: null,
				maximum_amount: null,
				payer: null,
				description: null
			}
		],
		bank_details_allowed: true,
		third_party_pension_provider: 'Legacy Pensions Ltd',
		fees: [{ name: 'Platform fee', frequency: 'monthly', rate: 0.0025 }],
		payments: [
			{ amount: 500, frequency: 'monthly', method: 'direct_debit', next_date: '2026-08-01' }
		],
		withdrawals: [],
		transfer: null,
		bank_accounts: null,
		client: { ...c, bank_account: { name: 'M Oakden', bank: 'Monzo', sort_code: '04-00-04' } },
		...over
	};
};

describe('AccountsListing (SSR)', () => {
	const html = (accounts: Account[]) =>
		render(AccountsListing, { props: { accounts, links } }).body;

	it('renders the book with INJECTED account + client links', () => {
		const body = html([account()]);
		expect(body).toContain('href="/x/accounts/seccl_AXF5AC13E"');
		expect(body).toContain('href="/x/clients/con_max"');
		expect(body).toContain('Oakden SIPP');
		expect(body).toContain('Max Oakden');
		expect(body).toContain('ID:');
		expect(body).toContain('>AXF5AC13E<');
	});

	it('pages complete-array mounts at 25 rows', () => {
		const accounts = Array.from({ length: 26 }, (_, index) =>
			account({ id: index + 1, uid: `account_${index + 1}`, name: `Account ${index + 1}` })
		);
		const body = html(accounts);
		expect(body).toContain('1–25 of 26');
		expect(body).toContain('Account 25');
		expect(body).not.toContain('Account 26');
	});

	it('states the service on each row, and says nothing where nothing is known', () => {
		expect(html([account()])).toContain('Discretionary');
		// A custodian-fed account with no capacity and no model: advisory and execution-only cannot
		// be told apart from what we hold, so the cell is an em-dash rather than a guessed service.
		const unknown = html([account({ service_type: null, model_id: null, model_name: null })]);
		expect(unknown).toContain('>Service type<');
		expect(unknown).not.toContain('Discretionary');
		expect(unknown).toContain('Filter by service type');
	});

	it('shows the actual model as a column and keeps its filter', () => {
		const body = html([account()]);
		// Advisory/discretionary was only a proxy for "has a model"; it could not say which mandate
		// the account actually follows. The value is now visible and filterable by stable model id.
		expect(body).toContain('>Model<');
		expect(body).toContain('Growth 60');
		expect(body).toContain('>Growth<');
		expect(body).toContain('Filter by model');
	});

	it('caps a long product type at its column instead of printing over Model', () => {
		// FB-20260921-09: Type is sized for SIPP and ISA, and a third-party offshore bond's code ran
		// out of its badge and over the Model column beside it. No service and no model here, so the
		// Type badge is the only capped badge on the row.
		const body = html([
			account({ product_type: 'TPOFFBOND', service_type: null, model_id: null, model_name: null })
		]);
		expect(body).toMatch(
			/max-w-full min-w-0[^"]*"[^>]*>[\s\S]{0,300}?class="[^"]*truncate[^"]*"[^>]*>[\s\S]{0,80}?TPOFFBOND/i
		);
	});

	it('renders an unlinked owner as an em-dash, not a broken link', () => {
		const body = html([account({ client: client({ id: null, uid: null }) })]);
		expect(body).not.toContain('/x/clients/');
	});

	it('distinguishes the two empty states (no book vs no matches)', () => {
		// The full stop went when the sentence became an empty state's TITLE rather than a line of
		// text in a table cell. What it must still say is that the book is empty rather than the
		// filter narrow — and it now says WHY, which is the half a bare sentence could not carry.
		const body = html([]);
		expect(body).toContain('No accounts yet');
		expect(body).not.toContain('match your filters');
		expect(body).toContain('Accounts appear as applications complete');
		// It offers nothing: opening an account is not an act of this screen.
		expect(body).not.toContain('Clear filters');
	});

	it('renders the OTHER half of that pair — the test above only ever proved one', () => {
		// The name said "the two empty states" and the body exercised the empty book alone, so the
		// half that actually changed (a narrowing emptying a populated book) was unpinned. SSR can
		// reach it through SERVER mode, where the query is a prop rather than internal state.
		const narrowed = render(AccountsListing, {
			props: {
				accounts: [],
				links,
				listing: { ...emptyQuery(), q: 'zzzz' },
				onquery: () => {}
			}
		}).body;
		expect(narrowed).toContain('No accounts match your filters');
		expect(narrowed).not.toContain('No accounts yet');
		// The narrowed branch had NO BODY — half a state.
		expect(narrowed).toContain('clear them to see every account');
		// AND IT CARRIES THE WAY OUT. `action={undefined}` was hardcoded here while `clear()` was
		// already wired to this panel's own toolbar.
		expect(narrowed).toContain('Clear filters');
	});

	it('counts a SEARCH TERM as a narrowing, not just the dropdowns', () => {
		// `dirty` tests the five filter dropdowns only. The title read it and the action read
		// `dirty || term`, so a search matching nothing said "No accounts yet" over a Clear button —
		// the two halves of one state disagreeing about which state it was.
		const body = render(AccountsListing, {
			props: {
				accounts: [],
				links,
				listing: { ...emptyQuery(), q: 'halloway' },
				onquery: () => {}
			}
		}).body;
		expect(body).toContain('No accounts match your filters');
		expect(body).not.toContain('No accounts yet');
	});
});

describe('TransactionsListing (SSR)', () => {
	const props = { transactions: transactionFixtures, links };

	it('says To approve, and keeps the status badge inside its column', () => {
		// "Awaiting approval" set the width of the Status column on its own, and spilled out of it
		// on a laptop (FB-20260921-09 on Transactions).
		const awaiting = { ...transactionFixtures[0], status: 'awaiting_approval' as const };
		const body = render(TransactionsListing, { props: { transactions: [awaiting], links } }).body;
		expect(body).toContain('To approve');
		expect(body).not.toContain('Awaiting approval');
		expect(body).toMatch(/max-w-full min-w-0[^"]*"[^>]*>[\s\S]{0,600}?To approve/);
	});

	it('renders the fixture corpus with INJECTED account + client links', () => {
		const body = render(TransactionsListing, { props }).body;
		expect(body).toContain('href="/x/accounts/seccl_AXF5AC13E"');
		expect(body).toContain('href="/x/clients/seccl_CXF421C15"');
	});

	it('prints a USD movement with a dollar, not a pound', () => {
		// TXN-4192 is the $25,000 payment — the currency column already said USD while the
		// amount cell always went through splitGBP.
		const body = render(TransactionsListing, { props }).body;
		expect(body).toMatch(/\$25,000/);
		expect(body).not.toContain('US$');
		expect(body).not.toMatch(/£25,000/);
	});

	it('distinguishes its THREE empty states, and only one of them offers a way out', () => {
		// It rendered one unconditional sentence — "No transactions match your filters" — on all
		// three, so an account with no movements yet and a firm whose feed has not landed both read
		// as a filter that had hidden the money.
		const firm = render(TransactionsListing, { props: { transactions: [], links } }).body;
		expect(firm).toContain('No transactions yet');
		expect(firm).toContain('as the custodian’s feed brings them in');
		expect(firm).not.toContain('match your filters');
		// Nothing to clear, so nothing is offered: a movement is not something this screen creates.
		expect(firm).not.toContain('Clear filters');

		// EMBEDDED (the account record's band) says which account it means.
		const account = render(TransactionsListing, {
			props: { transactions: [], links, embedded: true }
		}).body;
		expect(account).toContain('No transactions on this account yet');

		// NARROWED — and this is the one with an act behind it.
		const narrowed = render(TransactionsListing, {
			props: {
				transactions: [],
				links,
				listing: { ...emptyQuery(), q: 'zzzz' },
				onquery: () => {}
			}
		}).body;
		expect(narrowed).toContain('No transactions match your filters');
		expect(narrowed).toContain('clear them to see every movement');
		expect(narrowed).toContain('Clear filters');
	});

	it('counts the applied filters on the funnel, rather than opening the panel over the table', () => {
		const shut = render(TransactionsListing, {
			props: {
				...props,
				listing: { q: '', filters: {}, offset: 0, limit: 25, sort: null, dir: 'asc', keys: [] },
				matchCount: 1,
				facets: { status: ['settled'] },
				onquery: () => {}
			}
		}).body;
		expect(shut).toContain('aria-label="Show filters"');
		expect(shut).not.toContain('Filter by status');

		const applied = render(TransactionsListing, {
			props: {
				...props,
				listing: {
					q: '',
					filters: { status: ['settled'] },
					offset: 0,
					limit: 25,
					sort: null,
					dir: 'asc',
					keys: ['status']
				},
				matchCount: 1,
				facets: { status: ['settled'] },
				onquery: () => {}
			}
		}).body;
		expect(applied).toContain('aria-label="Hide filters (1 applied)"');
		expect(applied).not.toContain('Filter by status');
	});

	it('filters dates through the SHARED range control, and shows an applied one as a chip', () => {
		const bare = render(TransactionsListing, {
			props: {
				...props,
				listing: { q: '', filters: {}, offset: 0, limit: 25, sort: null, dir: 'asc', keys: [] },
				matchCount: 1,
				facets: {},
				onquery: () => {},
				showDateFilter: true
			}
		}).body;
		expect(bare).not.toContain('type="date"');

		const body = render(TransactionsListing, {
			props: {
				...props,
				listing: {
					q: '',
					filters: { date_from: '2026-01-01', date_to: '2026-06-30' },
					offset: 0,
					limit: 25,
					sort: null,
					dir: 'asc',
					keys: []
				},
				matchCount: 1,
				facets: {},
				onquery: () => {},
				showDateFilter: true
			}
		}).body;
		const bar = body.slice(body.indexOf('data-slot="filter-bar"'));
		expect(bar).toContain('Transaction date:');
		expect(bar).toContain(`${formatDate('2026-01-01')} – ${formatDate('2026-06-30')}`);
	});

	it('shows no balance, and no sign on an amount that carries no direction', () => {
		// `platform.transactions` stores every amount UNSIGNED — the conformed layer reads the
		// custodian's direction into `type` and publishes the figure without it. So a `+` here is
		// not "positive", it is "we do not know", and it appeared on withdrawals too. A running
		// balance is the same defect compounded: summing unsigned amounts ADDS a withdrawal.
		//
		// Both are out until the direction is carried in the figure itself. This pins their absence
		// rather than their look, so restoring either has to be a deliberate act with a test change.
		const body = render(TransactionsListing, { props }).body;
		expect(body).not.toContain('>Balance<');

		const amounts = props.transactions.map((t) => t.amount);
		expect(amounts.some((a) => a > 0)).toBe(true);
		// A rendered `+` would sit immediately before the currency symbol.
		expect(body).not.toContain('+£');
	});

	it('shows client/account public references beneath their names and calls null assets Cash', () => {
		const body = render(TransactionsListing, { props }).body;
		expect(body).toContain('Max Oakden');
		expect(body).toContain('CXF421C15');
		expect(body).toContain('Oakden SIPP');
		expect(body).toContain('AXF5AC13E');
		expect(body).toContain('Cash');
	});

	it('drops the Client column on one client’s own section, and keeps the shares at 100', () => {
		// The same wart as the Account pill on an account's holdings: on a client's Transactions
		// section every row belongs to that client, so the column repeats their name all the way down
		// and takes the width the account and the asset need. Its 12% goes to Account rather than
		// being left off the total — under `table-fixed` a short total hands the remainder to the last
		// column, which is how dropping one turns into an unevenly spaced table.
		const body = render(TransactionsListing, { props: { ...props, showClient: false } }).body;
		expect(body).not.toContain('>Client<');
		expect(body).toContain('>Account<');
		const shares = [...body.matchAll(/width:(\d+)%/g)].map((m) => Number(m[1]));
		expect(shares.reduce((a, b) => a + b, 0)).toBe(100);
	});

	it('keeps the Client column on the firm-wide ledger, which is what it is for', () => {
		const body = render(TransactionsListing, { props }).body;
		expect(body).toContain('>Client<');
	});

	it('supports a client-scoped date range and CSV export without an adviser facet', () => {
		const body = render(TransactionsListing, {
			props: {
				...props,
				showClient: false,
				showAdviserFilter: false,
				showDateFilter: true,
				searchPlaceholder: 'Search reference or account',
				exportHref: '/x/export.csv'
			}
		}).body;
		expect(body).toContain('lucide-funnel');
		// The export is a MENU now (Excel or CSV), and a closed dropdown renders its trigger but
		// none of its items on the server — so the observable thing here is the trigger. The trigger's
		// own shape is pinned by `export-menu.ssr.test.ts`; the format links are covered by neither,
		// because seeing them means opening the menu, which means mounting.
		// The export lives at the CARD'S FOOT now, beside the pager, not in the page header — and it
		// is labelled for the action rather than the resource, because one component serves them all.
		expect(body).toContain('aria-label="Export"');
		expect(body).not.toContain('Filter by adviser');
	});

	it('read-only by default: no snippet ⇒ no Action required queue', () => {
		expect(render(TransactionsListing, { props }).body).not.toContain('data-test-queue');
	});

	it('renders the injected work queue with exactly the outstanding items', () => {
		const actionRequired = createRawSnippet<[Transaction[]]>((items) => ({
			render: () => `<div data-test-queue>${items().length}</div>`
		}));
		const body = render(TransactionsListing, { props: { ...props, actionRequired } }).body;
		expect(body).toContain(`<div data-test-queue>${needsAction(transactionFixtures).length}</div>`);
	});

	describe('layout A: the pushed panel and the folding table', () => {
		it('opens nothing until a row is picked: no panel and no Close button', () => {
			// The table has the whole row until a movement is picked; a panel that held its width
			// with nothing in it is what the Contributions workbench gave up.
			const body = render(TransactionsListing, { props: { ...props, detail: 'panel' } }).body;
			expect(body).toContain('>Client<');
			expect(body).not.toContain('Selected transaction');
			expect(body).not.toContain('aria-label="Close panel"');
			expect(body).not.toContain('transaction-selected');
			expect(body).not.toContain('max-xl:hidden');
		});

		it('keeps the default mount on the drawer, with no panel markup', () => {
			// A client's section, an account's band and the admin book sit in smaller spaces, so
			// they keep the overlay drawer. Only a mount that asks for the panel gets its frame.
			for (const extra of [{}, { showClient: false }, { embedded: true, showAccount: false }]) {
				const body = render(TransactionsListing, { props: { ...props, ...extra } }).body;
				expect(body).not.toContain('Selected transaction');
				expect(body).not.toContain('Close panel');
				expect(body).not.toContain('xl:w-[380px]');
			}
		});

		it('folds its columns by the TABLE’s width, on every mount', () => {
			// Container rungs, not viewport ones: the same screen holds a wide table or a narrow one
			// depending on the panel, so the columns answer the width the rows actually have.
			// One fund purchase carries an ISIN, so its stand-in line has something to show.
			const transactions = transactionFixtures.map((t) =>
				t.quantity != null && t.asset ? { ...t, isin: 'GB00B3X7QG63' } : t
			);
			for (const detail of ['sheet', 'panel'] as const) {
				const body = render(TransactionsListing, { props: { transactions, links, detail } }).body;
				expect(body).toContain('hidden @7xl:table-cell'); // ISIN, Currency
				expect(body).toContain('hidden @5xl:table-cell'); // Reference, Units
				expect(body).toContain('hidden @4xl:table-cell'); // Account, Asset
				expect(body).toContain('hidden @2xl:table-cell'); // Type
				// The stand-in lines, shown exactly while their column is gone.
				expect(body).toContain('block @7xl:hidden'); // the currency code under the amount
				expect(body).toContain('hidden @5xl:block @7xl:hidden'); // the ISIN under the asset
				expect(body).toContain('hidden @4xl:block @5xl:hidden'); // the reference under the client
				expect(body).toContain('block @4xl:hidden'); // the account under the client
				expect(body).toContain('block @2xl:hidden'); // the type word under the amount
				const shares = [...body.matchAll(/width:(\d+)%/g)].map((m) => Number(m[1]));
				expect(shares.reduce((a, b) => a + b, 0)).toBe(100);
			}
		});

		it('keeps the Account and the Reference on a mount with no Client column to fold them under', () => {
			// One client's section: the account is the row's name, and the reference has no second
			// line to go to, so it keeps the rung it had.
			const body = render(TransactionsListing, { props: { ...props, showClient: false } }).body;
			expect(body).not.toContain('hidden @4xl:block @5xl:hidden');
			expect(body).toContain('hidden @3xl:table-cell'); // Reference, as before
			const account = body.indexOf('>Account<');
			expect(account).toBeGreaterThan(-1);
			const th = body.lastIndexOf('<th', account);
			expect(body.slice(th, account)).not.toContain('hidden @');
		});
	});
});

describe('AccountDetail cash breakdown (0117)', () => {
	const withAdministratorCash = () =>
		detail({
			cash: 2500,
			provider_positions: [
				{
					source: 'morgan-lloyd',
					cash: 1500.25,
					as_of: '2026-09-03',
					crystallised: 12000,
					uncrystallised: 88000
				}
			]
		});

	it('totals the cash across every place it sits', () => {
		// The thing a client actually wants to know, and could not see: a pension's money sits in
		// the custodian's account AND in the scheme's own bank account at the administrator, so
		// "Cash £2,500" was part of the answer.
		const body = render(AccountDetail, {
			props: { account: withAdministratorCash(), holdings: [], transactions: [], links }
		}).body;
		expect(body).toMatch(/£4,000<span class="[^"]*text-muted-foreground[^"]*">\.25<\/span>/);
	});

	it('says where the money is, naming the provider', () => {
		const body = render(AccountDetail, {
			props: { account: withAdministratorCash(), holdings: [], transactions: [], links }
		}).body;
		// The exact sub-line, not the bare word: the record has a "Custodian" details row of its
		// own, so `toContain('custodian')` would pass whether or not the breakdown rendered.
		expect(body).toContain('£2,500.00 custodian · £1,500.25 Morgan Lloyd');
	});

	it('shows no breakdown when there is only one place — every non-pension account', () => {
		// A breakdown of one line is noise, and Seccl/SEI remain the sole source of truth for
		// those accounts, so the API sends them no administrator row at all.
		const body = render(AccountDetail, {
			props: {
				account: detail({ cash: 2500, provider_positions: [] }),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toContain('£2,500');
		expect(body).not.toContain('£2,500.00 custodian');
	});

	it('renders unchanged when the loader supplied no positions at all', () => {
		// `undefined` is "not fetched" — an older API, or a surface that does not ask. It must
		// read as the custodian figure alone rather than as zero.
		const body = render(AccountDetail, {
			props: { account: detail({ cash: 2500 }), holdings: [], transactions: [], links }
		}).body;
		expect(body).toContain('£2,500');
		expect(body).not.toContain('£2,500.00 custodian');
	});

	it('subtracts an overdrawn scheme rather than flooring it at zero', () => {
		// `cam_getscheme` says the bank balance can be negative, and an overdrawn scheme is
		// something an adviser has to see.
		const body = render(AccountDetail, {
			props: {
				account: detail({
					cash: 2500,
					provider_positions: [
						{
							source: 'morgan-lloyd',
							cash: -250,
							as_of: '2026-09-03',
							crystallised: null,
							uncrystallised: null
						}
					]
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toContain('£2,250');
	});

	it('ignores a position that states no cash', () => {
		// A row carrying only the crystallisation split says nothing about cash, and counting it
		// as 0 would be indistinguishable from a scheme with an empty bank account.
		const body = render(AccountDetail, {
			props: {
				account: detail({
					cash: 2500,
					provider_positions: [
						{
							source: 'morgan-lloyd',
							cash: null,
							as_of: '2026-09-03',
							crystallised: 12000,
							uncrystallised: 88000
						}
					]
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toContain('£2,500');
		expect(body).not.toContain('£2,500.00 custodian');
	});

	it('never states the administrator’s fund value beside the custodian’s', () => {
		// The double count the whole design avoids: for a SIPP that figure is the valuation WE
		// pushed them, so it is the same investments the account's own `value` already states.
		const body = render(AccountDetail, {
			props: { account: withAdministratorCash(), holdings: [], transactions: [], links }
		}).body;
		expect(body).not.toContain('£100,000');
	});

	it('prefers the administrator’s crystallisation when the custodian also states one', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail({
					cash: 2500,
					provider_positions: [
						{
							source: 'morgan-lloyd',
							cash: 1500.25,
							as_of: '2026-09-03',
							crystallised: 12000,
							uncrystallised: 88000
						},
						{
							source: 'sei',
							cash: null,
							as_of: '2026-09-03',
							crystallised: 50000,
							uncrystallised: 50000
						}
					]
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toMatch(/£12,000<span class="[^"]*text-muted-foreground[^"]*">\.00<\/span>/);
		expect(body).toMatch(/£88,000<span class="[^"]*text-muted-foreground[^"]*">\.00<\/span>/);
		expect(body).not.toContain('£50,000.00');
		expect(body).not.toContain('£62,000');
	});
});

describe('AccountDetail ISA allowance', () => {
	const isa = (over: Partial<AccountDetailData> = {}) =>
		detail({
			name: 'Stocks and shares ISA',
			product_type: 'ISA',
			product_name: 'ISA',
			...over
		});

	it('states what has been used, and what is left', () => {
		const body = render(AccountDetail, {
			props: {
				account: isa({
					allowance_used: 5000,
					allowance_limit: 20000,
					allowance_remaining: 15000,
					allowance_tax_year: null
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toContain('Allowance used');
		expect(body).toMatch(/£5,000<span class="[^"]*text-muted-foreground[^"]*">\.00<\/span>/);
		expect(body).not.toContain('of £20,000.00');
		expect(body).toContain('£15,000.00 left');
	});

	it('does not clamp a flexible ISA to the statutory allowance', () => {
		const body = render(AccountDetail, {
			props: {
				account: isa({
					allowance_used: 6000,
					allowance_limit: 26000,
					allowance_remaining: 20000
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toMatch(/£6,000<span class="[^"]*text-muted-foreground[^"]*">\.00<\/span>/);
		expect(body).toContain('£20,000.00 left');
		expect(body).not.toContain('of £20,000.00');
		expect(body).not.toContain('of £26,000.00');
	});

	it('shows a remaining figure without inventing a limit', () => {
		const body = render(AccountDetail, {
			props: {
				account: isa({
					allowance_used: null,
					allowance_limit: null,
					allowance_remaining: 8000,
					allowance_tax_year: '2026/27'
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toContain('Available allowance');
		expect(body).toMatch(/£8,000<span class="[^"]*text-muted-foreground[^"]*">\.00<\/span>/);
		expect(body).toContain('2026/27');
		expect(body).not.toContain('used of');
	});

	it('draws nothing when the custodian has stated no allowance', () => {
		const body = render(AccountDetail, {
			props: { account: isa(), holdings: [], transactions: [], links }
		}).body;
		expect(body).not.toContain('Available allowance');
	});

	it('draws nothing for a zero limit', () => {
		const body = render(AccountDetail, {
			props: {
				account: isa({ allowance_used: 0, allowance_limit: 0, allowance_remaining: 0 }),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).not.toContain('Available allowance');
	});

	it('shows a fully subscribed allowance as nothing left', () => {
		const body = render(AccountDetail, {
			props: {
				account: isa({
					allowance_used: 20000,
					allowance_limit: 20000,
					allowance_remaining: 0
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toContain('Allowance used');
		expect(body).toMatch(/£20,000<span class="[^"]*text-muted-foreground[^"]*">\.00<\/span>/);
		expect(body).not.toContain('of £20,000.00');
		expect(body).toContain('£0.00 left');
	});
});

describe('AccountDetail (SSR)', () => {
	it('renders the record header and overview off the injected data', () => {
		const body = render(AccountDetail, {
			props: { account: detail(), holdings: [], transactions: [], links }
		}).body;
		expect(body).toContain('Oakden SIPP');
		// The provider prefix is stripped for display — see providerReference.
		expect(body).toContain('AXF5AC13E');
		expect(body).not.toContain('seccl_AXF5AC13E');
		expect(body).toContain('href="/x/clients/con_max"'); // owner link, injected
		expect(body).toContain('Adviser: Mark Alford');
		// The DISC is a wallet — the account glyph everywhere else in the product — not the
		// person the client record wears. A person above "Oakden SIPP" said the record was about
		// the owner; the owner is a fact on the row below, with the person glyph beside it.
		// (Svelte may put a hydration marker between the disc and its glyph — `<!--[3-->` — so the
		// match allows a comment there rather than pinning SSR's comment placement.)
		expect(body).toMatch(
			/size-12" aria-hidden="true" title="Oakden SIPP">(?:<!--[^>]*-->)?<svg[^>]*lucide-wallet/
		);
		// The owner's name wears the shared record-link affordance, not a look of its own — asserted
		// through the constant so this pins the DECISION (a linked name reads the same everywhere)
		// rather than a class string, which is what made it fail when the affordance was unified.
		expect(body).toContain(LINK_RECORD);
		// The reference, the owner, the adviser and the rest sit in a WRAPPING CHIP ROW under the
		// name — the same header the client record states its contact facts in — rather than in one
		// muted run-on sentence of dot separators. The reference is the copyable one: it is the value
		// somebody actually needs to paste somewhere else.
		expect(body).toContain('aria-label="Copy account reference"');
		expect(body).toContain('flex min-w-0 flex-wrap items-center gap-x-3');
		// Overview cards: account info, bank, fees, regular movements.
		expect(body).toContain('Account info');
		expect(body).not.toContain('>Details</');
		// The custodian is stated ONCE, in the header, and off `platform_name` when the provider key
		// is unset — which is most of the book. It had a row in Account info as well; two places for
		// one fact is two places for it to disagree.
		expect(body).toContain('Seccl');
		expect(body).toContain('Monzo');
		expect(body).toContain('Platform fee');
		expect(body).toContain('all apply equally');
		expect(body).toContain('0.25%');
		expect(body).toContain('No withdrawals set up');
		expect(body).toContain('data-slot="empty-state"');
		expect(body).not.toContain('None set up.');
		expect(body).not.toContain('Add one above.');

		// THE CHARGE, not the firm's pricing label. This card listed "Advised standard" and
		// "Platform charge" and nothing else, so the question it is read to answer — what is this
		// client paying, and how often — could only be answered from the fee catalogue.
		expect(body).toContain('Advised standard');
		expect(body).toContain('0.75% of the account value');
		expect(body).toContain('Ongoing');
		expect(body).toContain('Minimum £250');
		expect(body).toContain('VAT is charged on top');
		// A tiered package states every band, because the figure an account pays depends on which
		// one it sits in and "Tiered" on its own tells an adviser nothing.
		expect(body).toContain('Platform charge');
		expect(body).toContain('Tiered by account value');
		expect(body).toContain('Up to £250,000: 0.30%');
		expect(body).toContain('Over £250,000: 0.20%');
		// The client's own wording stays on the client's own dossier.
		expect(body).not.toContain('of your account value');
	});

	it('shows a pending bank change as the case and the field diff, not empty dashes', () => {
		const pending: ReviewRow = {
			uid: 'rev_acc_1',
			case_no: 12,
			action_key: 'account.bank_details_changed',
			label: "Change an account's bank details",
			summary: null,
			before_label: 'No bank details',
			after_label: '20-00-00 · ****5678',
			event_type: 'account.bank_accounts_updated',
			subject_type: 'account',
			subject_id: 'seccl_AXF5AC13E',
			contact_uid: 'con_max',
			contact_name: 'Max Oakden',
			account_uid: 'seccl_AXF5AC13E',
			account_name: 'Oakden SIPP',
			justification: 'Client moved banks',
			reviewer_role_id: 1,
			reviewer_role_slug: 'compliance',
			reviewer_role_name: 'Compliance',
			proposed_by: 'user-1',
			proposed_by_name: 'Gio Matonti',
			proposed_at: '2026-09-22T10:00:00Z',
			status: 'pending',
			decided_by: null,
			decided_by_name: null,
			decided_at: null,
			decision_note: null,
			applied_at: null,
			is_mine: true,
			can_decide: false,
			payload: {
				name: 'Gio Matonti',
				sort_code: '20-00-00',
				account_number: '12345678'
			},
			before: { value: null }
		};
		const body = render(AccountDetail, {
			props: {
				account: detail(),
				holdings: [],
				transactions: [],
				links: { ...links, review: (uid) => `/reviews?open=${uid}` },
				pendingReviews: [pending],
				onCancelReview: () => undefined
			}
		}).body;
		expect(body).toContain('RV-12');
		expect(body).toContain('Waiting');
		expect(body).toContain('Gio Matonti');
		expect(body).toContain('Client moved banks');
		expect(body).toContain('12345678');
		expect(body).toContain('20 - 00 - 00');
		expect(body).not.toContain('••••');
		expect(body).toContain('From');
		expect(body).toContain('href="/reviews?open=rev_acc_1"');
		expect(body).not.toContain('waiting for review');
	});

	it('prints a recent instruction in the account’s currency', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail({ currency: 'USD' }),
				holdings: [],
				transactions: [],
				links,
				instructions: [
					{
						uid: 'ins_usd',
						kind: 'expected_in',
						amount: 250,
						status: 'pending',
						due_date: null,
						note: null,
						created_at: '2026-08-01T00:00:00Z',
						account_id: 2,
						account_uid: 'seccl_AXF5AC13E',
						account_name: 'Oakden SIPP',
						model_name: null
					}
				]
			}
		}).body;
		// Money splits the pence, so the signed figure is `+$250` plus a `.00` span.
		expect(body).toContain('+$250');
		expect(body).not.toContain('+£');
		expect(body).not.toContain('US$');
	});

	it('restores account actions and renders the editable overview data without inventing routes', () => {
		const instructions = Array.from({ length: 6 }, (_, index) => ({
			uid: `ins_${index}`,
			kind: 'withdrawal' as const,
			amount: 100 + index,
			status: 'pending' as const,
			due_date: null,
			note: null,
			created_at: `2026-08-${String(index + 1).padStart(2, '0')}T00:00:00Z`,
			account_id: 2,
			account_uid: 'seccl_AXF5AC13E',
			account_name: 'Oakden SIPP',
			model_name: 'Growth 60'
		}));
		const accountDetail = detail();
		const body = render(AccountDetail, {
			props: {
				account: {
					...accountDetail,
					client: {
						...accountDetail.client,
						address: { line1: '12 High Street', city: 'York', postcode: 'YO1 1AA' }
					}
				},
				holdings: [],
				transactions: [],
				instructions,
				links,
				oninstruction: () => {},
				onchangemodel: () => {},
				onSaveBank: async () => null,
				onSaveFees: async () => null,
				onSaveMovements: async () => null,
				onschedule: () => {},
				contributionSummary: {
					taxYear: '2026/27',
					accountGross: 5000,
					count: 2
				}
			}
		}).body;
		const text = body.replace(/<!--.*?-->/g, '').replace(/\s+/g, ' ');
		// THE HEADING IS AN OUTLINE "ACTIONS" MENU — a closed DropdownMenu paints nothing at
		// SSR, so the body's only visible signal is the labelled trigger. Which items it holds
		// is a fact of the source; Invest and Sell (`model_topup` / `model_withdrawal`) stay
		// off this record. A SIPP does not Withdraw (that is Drawdown).
		expect(body).toContain('Account actions');
		expect(body).toContain('Actions');
		const source = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		expect(source).toContain('data-action="instruction-expected_in"');
		expect(source).toContain('data-action="instruction-withdrawal"');
		expect(source).toContain('!isPension');
		expect(source).toContain('data-action="instruction-adhoc_fee"');
		expect(source).toContain('data-action="change-model"');
		expect(source).toContain('Change model');
		expect(source).toContain('Payment');
		for (const kind of ['model_topup', 'model_withdrawal'])
			expect(source).not.toContain(`data-action="instruction-${kind}"`);
		expect(text).not.toContain('Pay in');
		expect(source).toContain('Ad hoc fee');
		// FIVE FACTS AND NO MORE. The custodian and its reference are stated in the header, the
		// product type is a badge beside the name, and custody is on the account's own row in the
		// listing — each was a row of a card that is two fifths of a row wide now.
		for (const gone of ['>Custodian<', '>Custodian reference<', '>Product<', '>Custody<'])
			expect(text).not.toContain(gone);
		// The service and the model are two statements: "Advisory" used to be printed here for
		// every account without a model, which called an execution-only account advised.
		expect(text).toContain('>Service type<');
		expect(text).toContain('>Model<');
		expect(text).toContain('>Currency<');
		expect(text).toContain('>Risk level<');
		expect(text).toContain('>Opened date<');
		expect(body).toContain('Advised standard');
		expect(body).toContain('Platform charge');
		expect(body).toContain('Legacy Pensions Ltd');
		// NO address panel: the address is the client's, it is on their record, and repeating it on
		// each of their accounts is one more place for it to go stale.
		expect(body).not.toContain('12 High Street');
		expect(body).not.toContain('aria-label="Edit address"');
		expect(body).toContain('Account info');
		expect(body).not.toContain('>Details<');
		expect(body).toContain('Edit bank details');
		expect(body).toContain('From');
		expect(body).toContain('Max Oakden');
		expect(body).toContain('M Oakden');
		expect(body).toContain('Monzo');
		expect(body).toContain('04 - 00 - 04');
		expect(body).toContain('grid-cols-[auto_minmax(0,1fr)]');
		expect(body).toContain('Edit fees');
		expect(body).not.toContain('Edit regular payments');
		expect(source).toContain('data-action="regular_payment"');
		expect(source).toContain('data-action="regular_withdrawal"');
		expect(source).toContain('Regular payment');
		expect(source).toContain('Regular withdrawal');
		expect(source).not.toContain('DropdownMenu.Label');
		expect(source).toContain('!isPension');
		{
			// ADDS LIVE IN THE ACTIONS MENU, not in the rail widget. A closed menu paints
			// nothing at SSR, so the card names what is on file and nothing else.
			const heading = body.slice(body.indexOf('Regular payments'), body.indexOf('Payments in'));
			expect(heading).not.toContain('data-slot="card-action"');
			expect(heading).not.toContain('Add payment');
			const paymentsSide = body.slice(body.indexOf('Payments in'), body.indexOf('Withdrawals'));
			expect(paymentsSide).not.toContain('data-action="regular_payment"');
			expect(paymentsSide).not.toContain('Add payment');
			expect(paymentsSide).toContain('Monthly');
			expect(paymentsSide).toContain('next');
			// The method is not on this line. A missing one used to print as an em dash
			// beside "next", and a present one wrapped the amount onto a third line.
			expect(paymentsSide).not.toContain('Direct debit');
			expect(paymentsSide).not.toContain('—');
			const withdrawals = body.slice(body.indexOf('Withdrawals'));
			expect(withdrawals).toContain('No withdrawals set up');
			expect(withdrawals).not.toContain('Add withdrawal');
		}
		expect(body).toContain('aria-label="Update this regular payment"');
		expect(body).not.toContain('aria-label="Remove this regular payment"');
		expect(body).not.toContain('A schedule held on this record');
		expect(body).not.toContain('Amend fees');
		expect(body).toContain('5 of 6');
		expect(body).toContain('This account');
		expect(body).not.toContain('Remaining annual allowance');
		expect(body).not.toContain('MPAA');
	});

	it('says so when the account has none of its own — it uses the client’s', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail({
					client: { ...client(), bank_account: null }
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).toContain('No bank details on the client');
		expect(body).toContain('Set at client or account level');
		expect(body).not.toContain('No bank details recorded for this client.');
		expect(body).not.toContain('Sort code');
		expect(body).not.toContain('Account number');
		expect(body).not.toContain('None on file yet.');
	});

	it('does not offer or display withdrawal bank details when the product disables them', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail({ product_type: 'JISA', bank_details_allowed: false }),
				holdings: [],
				transactions: [],
				links,
				onSaveBank: async () => null
			}
		}).body;
		expect(body).toContain(
			'Bank details do not apply to this product because withdrawals are not available.'
		);
		expect(body).not.toContain('Edit bank details');
		expect(body).not.toContain('This account uses the client’s bank details.');
		expect(body).not.toContain('From Max Oakden');
		expect(body).not.toContain('04 - 00 - 04');
	});

	it('does not say it uses the client’s once the account has its own', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail({
					bank_accounts: [
						{ name: 'Oakden SIPP', sort_code: '11-11-11', account_number: '11111111' }
					]
				}),
				holdings: [],
				transactions: [],
				links
			}
		}).body;
		expect(body).not.toContain('This account uses the client’s bank details.');
		expect(body).not.toContain('From Max Oakden');
		expect(body).toContain('11 - 11 - 11');
		expect(body).toContain('Oakden SIPP');
		expect(body).not.toContain('04 - 00 - 04');
	});

	it('edits the account’s own details — clearing falls back to the client’s', () => {
		const source = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		expect(source).toContain('accountBankSaveBody');
		expect(source).toMatch(/falls back to the\s+client['’]s/);
		expect(source).not.toContain(
			'This account uses the client’s bank details. Clearing every field removes them.'
		);
	});

	it('masks sort code and account number with the same journey field inputs', () => {
		const source = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		expect(source).toContain('<BankAccountFields');
		const editor = readFileSync(join(import.meta.dirname, '../clients/bank-fields.svelte'), 'utf8');
		expect(editor).toContain('<SortCodeInput');
		expect(editor).toContain('<BankAccountNumberInput');
		expect(source).not.toContain('placeholder="12-34-56"');
	});

	it('states the account’s five facts as label/value rows, the model the loudest', () => {
		// It was nine facts in a full-width four-column grid, which put the model — how the money is
		// actually being run — in a cell the size of the currency code. Five rows now, each label
		// against its value on ONE line, and the model above a rule at a larger weight.
		const { body } = render(AccountDetail, {
			props: { account: detail(), holdings: [], transactions: [], links, section: 'overview' }
		});
		const info = body.slice(body.indexOf('Account info'), body.indexOf('Bank details'));
		for (const fact of ['>Service type<', '>Currency<', '>Risk level<', '>Opened date<'])
			expect(info).toContain(fact);
		// The model leads, full width, above a 2×2 block of the other four.
		expect(info).toContain('grid grid-cols-2 gap-x-6 gap-y-3');
		expect(info).toContain('>Model<');
		expect(info.indexOf('>Model<')).toBeLessThan(info.indexOf('>Service type<'));
		expect(info).toContain('Growth 60');
		expect(info.indexOf('Growth 60')).toBeLessThan(info.indexOf('>Service type<'));
		// The ring beside it is drawn large: at the client rail's size it was a small mark against
		// a legend twice its height. Asserted on the source — this fixture has no allocation, so
		// the card does not render (a ring with no segments is one grey circle).
		const detailSource = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		expect(detailSource).toContain('size="lg"');
		const source = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		// Half the row each: the facts and the ring are the same question asked two ways.
		expect(source).toContain('grid gap-4 xl:grid-cols-2');
	});

	it('states four figures on tiles, on every band, and states each of them once', () => {
		// The same four the CLIENT record states, in the same words: what is held, what is liquid,
		// what it cost and what that has become. They were three bare `Stat`s in the top-right of the
		// header card — the layout the client record looked at and rejected — and "Growth" was the
		// firm's word for a figure the client record already calls an unrealised gain.
		const base = { account: detail(), holdings: [], transactions: [], links };
		const body = render(AccountDetail, { props: base }).body;
		for (const label of ['Value', 'Cash', 'Book cost', 'Unrealised gain / loss']) {
			expect(body).toContain(label);
		}
		expect(body).not.toContain('>Growth<');

		// ON EVERY BAND. The header carried them on Holdings and Transactions too, and those are the
		// bands where "what is this account worth" is the question the reader is holding while they
		// read the rows — so the tiles sit ABOVE the band rather than inside the overview.
		for (const section of ['overview', 'holdings', 'transactions'] as const) {
			const band = render(AccountDetail, { props: { ...base, section } }).body;
			expect(band, section).toContain('Book cost');
		}
	});

	it('says nothing about book cost unless every holding carries one', () => {
		// A sum over the holdings that HAVE a cost, set against a value for all of them, reads as a
		// gain nobody made. Every holding or none — the posture the client record takes when its
		// loader supplies no basis at all.
		const base = { account: detail(), transactions: [], links };
		const row = {
			name: 'Fund',
			wrapper: 'Oakden SIPP',
			units: 1,
			price: 1,
			value: 100,
			gainPct: null
		};
		const priced = { ...row, bookCost: 100 };
		const unpriced = { ...row };
		const mixed = render(AccountDetail, {
			props: { ...base, holdings: [priced, unpriced] }
		}).body;
		const bookCostOf = (body: string) =>
			body.slice(body.indexOf('Book cost'), body.indexOf('Book cost') + 400);
		expect(bookCostOf(mixed)).toContain('—');
		expect(
			bookCostOf(render(AccountDetail, { props: { ...base, holdings: [priced] } }).body)
		).toContain('£100');
	});

	it('is READ-ONLY when nothing is injected: no transfer/contribution/drawdown affordances', () => {
		const body = render(AccountDetail, {
			props: { account: detail(), holdings: [], transactions: [], links, canDrawdown: true }
		}).body;
		expect(body).not.toContain('Account actions');
		expect(body).not.toContain('data-action="new-transfer"');
		expect(body).not.toContain('data-action="change-model"');
		expect(body).not.toContain('data-action="contribution"');
		expect(body).not.toContain('data-action="drawdown"');
		expect(body).not.toContain('data-action="sync-to-morgan-lloyd"');
		expect(body).not.toContain('Edit bank details');
		expect(body).not.toContain('Edit fees');
		expect(body).not.toContain('data-action="regular_payment"');
		expect(body).not.toContain('data-action="regular_withdrawal"');
	});

	it('shows the action entry points when the app injects them', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail(),
				holdings: [],
				transactions: [],
				links: { ...links, newTransfer: (uid) => `/x/transfers/new?account=${uid}` },
				canDrawdown: true,
				ondrawdown: () => {}
			}
		}).body;
		expect(body).toContain('Account actions');
		const source = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		expect(source).toContain('links.newTransfer');
		expect(source).toContain('data-action="drawdown"');
		expect(source).not.toContain('data-action="contribution"');
		expect(source).toContain('!isPension');
		expect(source).toContain('data-action="push-to-administrator"');
	});

	it('offers the administrator push in the actions menu when the app injects it', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail(),
				holdings: [],
				transactions: [],
				links,
				canPushToAdministrator: true,
				onpushtoadministrator: () => {}
			}
		}).body;
		expect(body).toContain('Account actions');
		const source = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		expect(source).toContain('data-action="push-to-administrator"');
		expect(source).toContain('Push to administrator');
		expect(source).toContain('canDrawdown && ondrawdown');
	});

	// ── The record's bands: order, containment, and the pill that said nothing ──────────────
	//
	// A single band FILLS the screen and scrolls its own rows; a stacked mount (the admin book,
	// which has no routes for the bands) keeps page scroll through all three. Both halves are
	// asserted, because getting one right and the other wrong is the failure that ships: an
	// `overflow-hidden` column with nothing scrollable inside it CLIPS, silently.

	const HOLDINGS = [
		{
			name: 'Small Position Fund',
			ticker: 'IE00SMALL',
			wrapper: 'Oakden SIPP',
			units: 1,
			price: 1,
			value: 10,
			gainPct: 1
		},
		{
			name: 'Big Position Fund',
			ticker: 'IE00BIG',
			isin: 'IE00BIG',
			sedol: 'B1GBIG1',
			currency: 'USD',
			wrapper: 'Oakden SIPP',
			units: 9,
			price: 9,
			value: 9000,
			bookCost: 8000,
			unrealisedGain: 1000,
			href: '/x/accounts/seccl_AXF5AC13E/transactions?isin=IE00BIG',
			gainPct: 9
		}
	];

	it('leads the holdings with the biggest position, not the order the feed sent', () => {
		// Value, descending. A holdings table is read to find where the money is; feed order buries
		// it. The rows arrive smallest-first here precisely so a pass-through would fail.
		const body = render(AccountDetail, {
			props: { account: detail(), holdings: HOLDINGS, transactions: [], links, section: 'holdings' }
		}).body;
		expect(body.indexOf('Big Position Fund')).toBeLessThan(body.indexOf('Small Position Fund'));
	});

	it('splits the overview’s top-holding value and gain into pounds and pence', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail(),
				holdings: [{ ...HOLDINGS[1], value: 9000.5, unrealisedGain: 1000.25 }],
				transactions: [],
				links,
				section: 'overview'
			}
		}).body;
		expect(body).toMatch(/\$9,000<span[^>]*>\.50<\/span>/);
		expect(body).toMatch(/\+\$1,000<span[^>]*>\.25<\/span>/);
	});

	it('drops the Account pill on the account’s own holdings — it repeated this account’s name', () => {
		const band = render(AccountDetail, {
			props: { account: detail(), holdings: HOLDINGS, transactions: [], links, section: 'holdings' }
		}).body;
		// The column header goes with it; the instrument names stay.
		expect(band).not.toContain('>Account<');
		expect(band).toContain('Big Position Fund');
	});

	it('shows instrument ids, book cost and unrealised gain in ledger order', () => {
		const body = render(AccountDetail, {
			props: { account: detail(), holdings: HOLDINGS, transactions: [], links, section: 'holdings' }
		}).body;
		const order = [
			'>Investment<',
			'>ISIN<',
			'>SEDOL<',
			'>Units<',
			'>Currency<',
			'>Price<',
			'>Book cost<',
			'>Value<',
			'>Unrealised gain<'
		].map((h) => body.indexOf(h));
		expect(order.every((i) => i >= 0)).toBe(true);
		expect([...order].sort((a, b) => a - b)).toEqual(order);
		expect(body).toContain('USD');
		expect(body).not.toContain('US$');
		expect(body).not.toContain('USD 9.00');
		expect(body).toContain('B1GBIG1');
		expect(body).toContain('href="/x/accounts/seccl_AXF5AC13E/transactions?isin=IE00BIG"');
	});

	it('uses the firm ledger order without repeating the scoped client and account', () => {
		const body = render(AccountDetail, {
			props: { account: detail(), holdings: [], transactions: [], links, section: 'transactions' }
		}).body;
		const order = [
			'>Date<',
			'>Reference<',
			'>Type<',
			'>Asset<',
			'>Units<',
			'>ISIN<',
			'>Currency<',
			'>Amount<',
			'>Status<'
		].map((h) => body.indexOf(h));
		expect(order.every((i) => i >= 0)).toBe(true);
		expect([...order].sort((a, b) => a - b)).toEqual(order);
		expect(body).not.toContain('>Client<');
		expect(body).not.toContain('>Account<');
		// No Balance column: a running total of unsigned amounts is not a balance.
		expect(body).not.toContain('>Balance<');
		expect(body).not.toContain('>Subtype<');
		expect(body).not.toContain('>Method<');
	});

	it('honours an explicit `contained` over the section default', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail(),
				holdings: HOLDINGS,
				transactions: [],
				links,
				section: 'holdings',
				contained: false
			}
		}).body;
		expect(body).not.toContain('md:min-h-0 md:flex-1');
	});

	it('keeps the pension actions pension-only even when the callbacks exist', () => {
		const body = render(AccountDetail, {
			props: {
				account: detail({ product_type: 'GIA', discretionary: false, model_name: null }),
				holdings: [],
				transactions: [],
				links,
				canDrawdown: false,
				ondrawdown: () => {}
			}
		}).body;
		expect(body).not.toContain('data-action="contribution"');
		expect(body).not.toContain('data-action="drawdown"');
	});

	it('lets a GIA add or remove a regular withdrawal, and a SIPP neither', () => {
		const schedule = {
			holdings: [],
			transactions: [],
			links,
			onSaveMovements: async () => null,
			onschedule: () => {}
		};
		const gia = render(AccountDetail, {
			props: {
				...schedule,
				account: detail({
					product_type: 'GIA',
					is_pension: false,
					product_name: 'General Investment Account',
					withdrawals: [
						{ amount: 250, frequency: 'quarterly', method: 'bacs', next_date: '2026-10-01' }
					]
				})
			}
		}).body;
		expect(gia).toContain('Account actions');
		expect(gia).toContain('aria-label="Update this regular withdrawal"');
		expect(gia).not.toContain('Bacs');
		{
			// ADDS LIVE IN THE ACTIONS MENU. The widget is the schedule; a closed menu
			// paints nothing at SSR, so GIA vs SIPP is a fact of the source guard.
			const heading = gia.slice(gia.indexOf('Regular payments'), gia.indexOf('Payments in'));
			expect(heading).not.toContain('data-slot="card-action"');
			expect(heading).not.toContain('Add payment');
			const payments = gia.slice(gia.indexOf('Payments in'), gia.indexOf('Withdrawals'));
			expect(payments).not.toContain('data-action="regular_payment"');
			expect(payments).not.toContain('Add payment');
			const withdrawals = gia.slice(gia.indexOf('Withdrawals'));
			expect(withdrawals).not.toContain('data-action="regular_withdrawal"');
			expect(withdrawals).not.toContain('Add withdrawal');
		}
		const source = readFileSync(join(import.meta.dirname, 'account-detail.svelte'), 'utf8');
		expect(source).toContain('data-action="regular_payment"');
		expect(source).toContain('data-action="regular_withdrawal"');
		expect(source).toContain('Regular payment');
		expect(source).toContain('Regular withdrawal');
		expect(source).not.toContain('DropdownMenu.Label');
		expect(source).toContain('!isPension');

		const sipp = render(AccountDetail, {
			props: {
				...schedule,
				account: detail({
					withdrawals: [
						{ amount: 250, frequency: 'quarterly', method: 'bacs', next_date: '2026-10-01' }
					]
				})
			}
		}).body;
		expect(sipp).toContain('Account actions');
		expect(sipp).not.toContain('aria-label="Update this regular withdrawal"');
		expect(sipp).toContain('aria-label="Update this regular payment"');
		{
			const heading = sipp.slice(sipp.indexOf('Regular payments'), sipp.indexOf('Payments in'));
			expect(heading).not.toContain('data-slot="card-action"');
			const payments = sipp.slice(sipp.indexOf('Payments in'), sipp.indexOf('Withdrawals'));
			expect(payments).not.toContain('Add payment');
			expect(heading).not.toContain('Add withdrawal');
		}
	});

	it('uses the shared empty state when a side has no schedule', () => {
		const schedule = {
			holdings: [],
			transactions: [],
			links,
			onschedule: () => {}
		};
		const gia = render(AccountDetail, {
			props: {
				...schedule,
				account: detail({
					product_type: 'GIA',
					is_pension: false,
					product_name: 'General Investment Account',
					payments: [],
					withdrawals: []
				})
			}
		}).body;
		expect(gia).toContain('No payments set up');
		expect(gia).toContain('No withdrawals set up');
		expect(gia).not.toContain('None set up.');
		// THE WAY OUT IS THE ACTIONS MENU. The card is one muted line per empty side — a
		// dashed EmptyState plus an outline button was a widget made of the absence it named.
		{
			const start = gia.indexOf('Regular payments');
			const next = gia.indexOf('Recent instructions', start);
			const card = gia.slice(start, next === -1 ? start + 2500 : next);
			expect(card).toContain('data-slot="empty-state"');
			expect(card).not.toContain('data-slot="ghost-list"');
			expect(card).not.toContain('border-dashed');
			expect(card).not.toContain('Add one above.');
			expect(card).not.toContain('Add payment');
			expect(card).not.toContain('Add withdrawal');
		}
		expect(gia).toContain('Account actions');

		const sipp = render(AccountDetail, {
			props: {
				...schedule,
				account: detail({ payments: [], withdrawals: [] })
			}
		}).body;
		expect(sipp).toContain('No payments set up');
		expect(sipp).toContain('No withdrawals set up');
		// A SIPP cannot add a regular withdrawal at all, so that side names the absence and
		// points at the administrator. The Add stays in the menu, and only for payments.
		expect(sipp).not.toContain('Add payment');
		expect(sipp).not.toContain('Add withdrawal');
		expect(sipp).toContain('Ask the administrator to set one up.');
	});
});

describe("AccountDetail — the owner's vulnerability (SSR)", () => {
	const flagged = (over: Partial<AccountClient> = {}) =>
		detail({
			client: {
				...client(),
				bank_account: null,
				is_vulnerable: true,
				vulnerability_reasons: ['health', 'capability'],
				vulnerability_is_temporary: false,
				vulnerability_recorded_at: '2026-07-30T09:00:00Z',
				vulnerability_notes: 'Prefers phone contact; confirm in writing after.',
				...over
			}
		});

	const html = (data: AccountDetailData) =>
		render(AccountDetail, { props: { account: data, holdings: [], transactions: [], links } }).body;

	it('states the flag on the account header, exactly as the client record does', () => {
		// The account page is a place someone's money is moved, so the flag has to travel with the
		// account rather than living only on the record an adviser may not have opened.
		const body = html(flagged());
		expect(body).toContain('Vulnerable client');
		expect(body).toContain('Health');
		expect(body).toContain('Capability');
		expect(body).toContain('Permanent');
		expect(body).toContain('Prefers phone contact');
		expect(body).toContain('bg-destructive-subtle');
	});

	it('never offers to edit it from the account', () => {
		// Recording vulnerability belongs on the person, not on one of their accounts — the strip is
		// the same component with no `onedit`, so there is no Update button to find.
		expect(html(flagged())).not.toContain('>Update<');
	});

	it('says nothing for an unflagged owner, or one whose block was not fetched', () => {
		expect(html(detail())).not.toContain('Vulnerable client');
		expect(html(flagged({ is_vulnerable: undefined }))).not.toContain('Vulnerable client');
	});
});

describe('AccountDetail — the bands became routes', () => {
	const render_ = (section?: 'overview' | 'holdings' | 'transactions' | 'all') =>
		render(AccountDetail, {
			props: {
				account: detail(),
				holdings: [
					{
						name: 'Vanguard FTSE Global',
						wrapper: 'SIPP',
						units: 100,
						price: 2,
						value: 200,
						gainPct: 4
					}
				],
				transactions: [],
				links,
				...(section ? { section } : {})
			}
		}).body;

	it('renders only the band it is asked for', () => {
		// The TABLE is the band, not the instrument's name: the overview now summarises the biggest
		// positions in its Holdings card, so a name appearing there is the point rather than a leak
		// from the band next door. `aria-label="Holdings"` is the table itself.
		const overview = render_('overview');
		expect(overview).toContain('Bank details');
		expect(overview).not.toContain('aria-label="Holdings"');

		const holdings = render_('holdings');
		expect(holdings).toContain('aria-label="Holdings"');
		expect(holdings).toContain('Vanguard FTSE Global');
		expect(holdings).not.toContain('Bank details');
	});

	it('draws the header ONCE on the whole record', () => {
		// It is a snippet rendered in two places — inside the overview's main column, so the rail
		// runs level with the account's name, and full width on a table band, which has no rail. On
		// `all` both of those render, and the header appeared twice: a full-width hero above the
		// inset one.
		// The `<h1>` is the header's own, and a record has one name however many bands it draws.
		const all = render_('all');
		expect([...all.matchAll(/<h1/g)]).toHaveLength(1);
		expect([...render_('holdings').matchAll(/<h1/g)]).toHaveLength(1);
		expect([...render_('overview').matchAll(/<h1/g)]).toHaveLength(1);
	});

	it('keeps the header on every band', () => {
		// The value/growth/cash figures and the money triggers are what an adviser needs whichever
		// band they are on, so the header is not one of the bands.
		for (const section of ['overview', 'holdings', 'transactions'] as const) {
			expect(render_(section), section).toContain('Value');
		}
	});

	it('DEFAULTS to the whole record, not to a third of it', () => {
		// A mount with no routes for the bands — the admin portal's read-only one — must not lose
		// two thirds of the account by saying nothing. Defaulting to `overview` did exactly that.
		const all = render_();
		expect(all).toContain('Bank details');
		expect(all).toContain('Vanguard FTSE Global');
		// And the stacked form names its bands, since no nav did it on the way in.
		expect(all).toContain('Holdings');
		expect(all).toContain('Transactions');
	});

	it('has no tab strip left', () => {
		// The strip is the sidebar now. A `role="tablist"` here would mean two navigations for one
		// record, disagreeing about which band is showing.
		expect(render_('overview')).not.toContain('role="tablist"');
		expect(render_()).not.toContain('role="tablist"');
	});
});

describe('the product mapping (0128)', () => {
	// THE SHELF ITEM IS NOT ON THE RECORD. It had a row of its own in Account info, naming the
	// product and its code so a wrong parent was visible, and warning "not on the shelf" when an
	// account had no parent at all (98 of 379 on the dev book). Account info carries five facts now
	// and the product is not one of them — the header states the account TYPE as a badge, which is
	// the account's own column and so cannot be wrong about a mapping it never reads.
	it('states the account type as a badge and claims nothing about the shelf', () => {
		const { body } = render(AccountDetail, {
			props: {
				account: {
					...detail(),
					product_name: 'Self-Invested Personal Pension',
					product_code: 'SIPP',
					product_type: 'SIPP'
				},
				holdings: [],
				transactions: [],
				links
			}
		});
		expect(body).toContain('SIPP');
		// Never the shelf NAME dressed up as the account's own: that was the 0128 defect.
		expect(body).not.toContain('Self-Invested Personal Pension');
	});

	it('does not dress the enum up as a product when there is no shelf item', () => {
		const { body } = render(AccountDetail, {
			props: {
				account: { ...detail(), product_name: null, product_code: null, product_type: 'SIPP' },
				holdings: [],
				transactions: [],
				links
			}
		});
		expect(body).toContain('SIPP');
		expect(body).not.toContain('>Product<');
	});
});

describe('accounts listing local-mode search', () => {
	it('Clear filters also resets the search box', () => {
		const source = readFileSync(join(import.meta.dirname, 'accounts-listing.svelte'), 'utf8');
		expect(source).toMatch(/modelFilter = ALL;[\s\S]*localQuery = '';/);
	});
});

describe('the transaction drawer is a slim rail', () => {
	it('opts out of the 58vw console and keeps each value beside its label', () => {
		// The primitive's drawer is an illustration console. This one is a peek at one movement,
		// and a 30% value cap inside that console left a channel of empty space a label had to
		// cross. A source scan, because a later edit that puts the console width back — or
		// `justify-between` on the pairs — ships the split layout again.
		const source = readFileSync(join(import.meta.dirname, 'transactions-listing.svelte'), 'utf8');
		const drawer = source.slice(source.indexOf('<Sheet.Root')).replace(/<!--[\s\S]*?-->/g, '');
		expect(drawer).toContain('sm:!w-[26rem]');
		const lists = drawer.slice(drawer.indexOf('</StatusBadge>'));
		expect(lists).not.toContain('justify-between');
		expect(lists).not.toContain('text-right');
		expect(lists.split('grid-cols-[auto_minmax(0,1fr)]').length - 1).toBe(2);
	});
});

describe('AccountDetail holdings breakdown', () => {
	const holding = (name: string, value: number, ticker?: string) => ({
		name,
		wrapper: 'ISA',
		units: 1,
		price: value,
		value,
		gainPct: null,
		...(ticker ? { ticker } : {})
	});

	it('states how the money is arranged and what the biggest positions are', () => {
		// The two questions an account is opened for. The four figures above say what it is WORTH;
		// this says what it is IN — and the second used to cost a trip to the Holdings tab.
		const body = render(AccountDetail, {
			props: {
				account: detail({ value: 1000 }),
				holdings: [holding('Global Equity Fund', 600, 'GEF'), holding('Gilt Fund', 200)],
				transactions: [],
				links,
				section: 'overview' as const,
				allocations: [
					{ label: 'Equities', value: 600 },
					{ label: 'Fixed income', value: 200 }
				]
			}
		}).body;
		expect(body).toContain('Holdings');
		expect(body).toContain('Equities');
		expect(body).toContain('Fixed income');
		// The share is of the POSITIONS, not of the account's value: the two are struck at different
		// times by different systems, and dividing by an account value the lake disagrees with
		// printed shares like "743% of account". One line per row now, so the share is the bare
		// percentage between the name and the value.
		expect(body).toContain('75.0%');
		// The ticker is NOT on this row: one line per position, the identifier is on the band.
		expect(body).not.toContain('GEF');
	});

	it('lists every position and scrolls, rather than truncating', () => {
		const many = Array.from({ length: 8 }, (_, i) => holding(`Fund ${i + 1}`, 100 - i));
		const body = render(AccountDetail, {
			props: {
				account: detail({ value: 800 }),
				holdings: many,
				transactions: [],
				links,
				section: 'overview' as const
			}
		}).body;
		// REVERSED: five with "3 more under Holdings" under them sent the reader to another band to
		// see a list that fits in the column they were already looking at. The card scrolls, and
		// caps its own height so a sixty-line portfolio cannot push the overview off the page.
		expect(body).toContain('Fund 5');
		expect(body).toContain('Fund 8');
		expect(body).not.toContain('more under Holdings');
		expect(body).toContain('overflow-y-auto');
	});

	it('keeps a ghost ring for an account nobody has valued', () => {
		// Hiding the card left Account info alone in a two-column grid. The empty
		// donut is the filled size: same hole, a caption under it, centred in the card.
		const body = render(AccountDetail, {
			props: {
				account: detail(),
				holdings: [],
				transactions: [],
				links,
				section: 'overview' as const,
				allocations: []
			}
		}).body;
		expect(body).toContain('No allocation yet');
		expect(body).toContain('No holdings yet');
		expect(body).toContain('flex flex-1 flex-col items-center justify-center');
		expect(body).not.toContain('data-slot="ghost-list"');
	});
});
