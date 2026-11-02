/**
 * The account book's pure transaction rules, moved here from apps/platform
 * `$lib/transactions/types` (the old path is now a shim over these exports).
 *
 * `needsAction` is the Action required queue's admission rule — an item that wrongly enters
 * asks staff to categorise something that doesn't need it, and one that wrongly stays out
 * silently skips an HMRC-reportable decision. Every clause gets a case.
 */
import { describe, expect, it } from 'vitest';
import {
	CONTRIBUTION_CATEGORIES,
	PENSION_PRODUCTS,
	type Transaction,
	chargeBreakdown,
	entitlementLabel,
	isPensionProduct,
	needsAction,
	subtypeDetail
} from './transactions.js';
import { transactionFixtures } from './transaction-fixtures.js';

const txn = (over: Partial<Transaction> = {}): Transaction => ({
	uid: 'TXN-1',
	date: '2026-07-27',
	account_id: 1,
	account_name: 'Oakden SIPP',
	account_uid: 'seccl_AX1',
	client_id: 1,
	client_name: 'Max Oakden',
	adviser: 'Mark Alford',
	type: 'payment',
	subtype: 'Payment in',
	method: 'bank_transfer',
	amount: 1000,
	asset: null,
	quantity: null,
	status: 'settled',
	product_type: 'SIPP',
	needs_categorisation: true,
	...over
});

describe('needsAction', () => {
	it('admits an uncategorised pension payment in', () => {
		expect(needsAction([txn()])).toHaveLength(1);
	});

	it('drops an item the moment it carries a category — resolving it leaves the queue', () => {
		expect(needsAction([txn({ category: 'transfer_in' })])).toHaveLength(0);
	});

	it('never asks about money OUT — a withdrawal is not a contribution', () => {
		expect(needsAction([txn({ amount: -1000 })])).toHaveLength(0);
		expect(needsAction([txn({ amount: 0 })])).toHaveLength(0);
	});

	it('only pensions carry the HMRC question — an ISA payment in never queues', () => {
		expect(needsAction([txn({ product_type: 'ISA' })])).toHaveLength(0);
	});

	it('matches the product type case-insensitively (lake vocab is not guaranteed upper)', () => {
		expect(needsAction([txn({ product_type: 'sipp' })])).toHaveLength(1);
	});

	it('ignores rows the feed never flagged', () => {
		expect(needsAction([txn({ needs_categorisation: undefined })])).toHaveLength(0);
	});
});

describe('isPensionProduct', () => {
	it('is PENSION_PRODUCTS with the null/case handling the pages need', () => {
		for (const p of PENSION_PRODUCTS) expect(isPensionProduct(p)).toBe(true);
		expect(isPensionProduct('sipp')).toBe(true);
		expect(isPensionProduct('ISA')).toBe(false);
		expect(isPensionProduct(null)).toBe(false);
		expect(isPensionProduct(undefined)).toBe(false);
		expect(isPensionProduct('')).toBe(false);
	});
});

describe('CONTRIBUTION_CATEGORIES', () => {
	it('offers every category exactly once (the form renders this list verbatim)', () => {
		const values = CONTRIBUTION_CATEGORIES.map((c) => c.value);
		expect(new Set(values).size).toBe(values.length);
		expect(values).toContain('not_a_contribution');
	});
});

describe('transactionFixtures (this directory’s test corpus — not exported, not app data)', () => {
	it('has unique uids — they are the DataTable row keys', () => {
		const uids = transactionFixtures.map((t) => t.uid);
		expect(new Set(uids).size).toBe(uids.length);
	});

	it('exercises the Action required queue (the demo must show the feature)', () => {
		expect(needsAction(transactionFixtures).length).toBeGreaterThan(0);
	});

	it('keeps the orders-only rule: asset/quantity appear on orders alone', () => {
		for (const t of transactionFixtures.filter((t) => t.quantity != null)) {
			expect(t.type, t.uid).toBe('order');
		}
	});
});

describe('subtypeDetail (folding Subtype into Type)', () => {
	it('drops the vendor word when it only restates the canonical type', () => {
		// The whole reason the two columns became one: on this book most rows carried the same word in
		// both, so a column's width was spent rendering "Dividend · Dividend" down the screen.
		expect(subtypeDetail({ type: 'dividend', subtype: 'Dividend' })).toBeNull();
		expect(subtypeDetail({ type: 'withdrawal', subtype: 'Withdrawal' })).toBeNull();
	});

	it('matches on letters, so punctuation and case cannot smuggle the redundancy back', () => {
		// `transfer_in` and "Transfer In" are the same statement; comparing raw strings would show both.
		expect(subtypeDetail({ type: 'transfer_in', subtype: 'Transfer In' })).toBeNull();
		expect(subtypeDetail({ type: 'transfer_in', subtype: 'transfer-in' })).toBeNull();
	});

	it('keeps the vendor word when it says something the type does not', () => {
		// This is what the field is FOR: which fee, which kind of deposit, how the order was placed.
		expect(subtypeDetail({ type: 'fee', subtype: 'Platform Fee' })).toBe('Platform Fee');
		expect(subtypeDetail({ type: 'deposit', subtype: 'Employee' })).toBe('Employee');
		expect(subtypeDetail({ type: 'buy', subtype: 'At Best' })).toBe('At Best');
		expect(subtypeDetail({ type: 'transfer_in', subtype: 'Internal' })).toBe('Internal');
	});

	it('sentence-cases the vendor word, so the ledger does not mix casings', () => {
		// The type beside it is always capitalised (`humanise`), so a lower-case vendor word read as a
		// different kind of thing one row down: "Deposit: cash receipt" against "Deposit: Cash Receipt".
		expect(subtypeDetail({ type: 'deposit', subtype: 'cash receipt' })).toBe('Cash receipt');
		expect(subtypeDetail({ type: 'buy', subtype: 'at best' })).toBe('At best');
	});

	it('prints Graphene origin kinds as words, never with an underscore', () => {
		expect(subtypeDetail({ type: 'deposit', subtype: 'expected_in' })).toBe('Payment');
		expect(subtypeDetail({ type: 'fee', subtype: 'adhoc_fee' })).toBe('Adviser fee');
		expect(subtypeDetail({ type: 'deposit', subtype: 'personal_contribution' })).toBe(
			'Personal contribution'
		);
		expect(subtypeDetail({ type: 'deposit', subtype: 'model_topup' })).toBe('Model top-up');
		expect(subtypeDetail({ type: 'withdrawal', subtype: 'withdrawal' })).toBeNull();
	});

	it('leaves the rest of the vendor word alone', () => {
		// Only the first letter. "Platform Fee" is the vendor's capitalisation of a proper term, and
		// re-casing the rest would be this code deciding it knows better about a word it never saw.
		expect(subtypeDetail({ type: 'fee', subtype: 'Platform Fee' })).toBe('Platform Fee');
		expect(subtypeDetail({ type: 'buy', subtype: 'DCA order' })).toBe('DCA order');
	});

	it('has nothing to show for a blank subtype rather than an empty line', () => {
		for (const subtype of ['', '   ', null as unknown as string]) {
			expect(subtypeDetail({ type: 'fee', subtype })).toBeNull();
		}
	});
});

describe('chargeBreakdown', () => {
	it('states the split when it says something the total does not', () => {
		expect(chargeBreakdown({ fee_amount: 6.5, dealing_charges: 4.5, other_charges: 2 })).toEqual([
			{ label: 'Dealing', amount: 4.5 },
			{ label: 'Other', amount: 2 }
		]);
	});

	it('says nothing when one component IS the whole charge', () => {
		expect(chargeBreakdown({ fee_amount: 4.5, dealing_charges: 4.5, other_charges: null })).toEqual(
			[]
		);
	});

	it('drops a zero component rather than stating it', () => {
		expect(chargeBreakdown({ fee_amount: 2, dealing_charges: 0, other_charges: 2 })).toEqual([]);
	});

	it('says nothing at all for a provider that publishes no split', () => {
		expect(
			chargeBreakdown({ fee_amount: 12.4, dealing_charges: null, other_charges: null })
		).toEqual([]);
	});
});

describe('entitlementLabel', () => {
	it('calls it the EX-DATE on income', () => {
		expect(entitlementLabel({ type: 'income' })).toBe('Ex-date');
		expect(entitlementLabel({ type: 'order', family: 'dividend' })).toBe('Ex-date');
		expect(entitlementLabel({ type: 'order', family: 'interest' })).toBe('Ex-date');
	});

	it('calls it the trade date on everything else', () => {
		for (const type of ['payment', 'order', 'fee', 'transfer'] as const) {
			expect(entitlementLabel({ type })).toBe('Traded');
		}
	});
});
