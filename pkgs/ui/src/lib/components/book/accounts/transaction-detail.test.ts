/**
 * The transaction drawer's field rules. What matters here is what is LEFT OUT: a cash movement
 * carries no asset, no ISIN and no unit count, and a panel of em-dashes claiming otherwise reads
 * as a ledger that has lost them.
 */
import { describe, expect, it } from 'vitest';
import { transactionDetailGroups } from './transaction-detail.js';
import { conversionCaption, type Transaction } from './transactions.js';

const txn = (over: Partial<Transaction> = {}): Transaction => ({
	uid: 'seccl:AB12345',
	date: '2026-07-27',
	account_id: 1,
	account_name: 'Oakden SIPP',
	account_uid: 'seccl_AX1',
	client_id: 1,
	client_name: 'Max Oakden',
	adviser: 'Mark Alford',
	type: 'payment',
	subtype: 'Cash receipt',
	method: 'bank_transfer',
	amount: 1000,
	asset: null,
	quantity: null,
	status: 'settled',
	product_type: 'SIPP',
	...over
});

const find = (t: Transaction, label: string) =>
	transactionDetailGroups(t)
		.flatMap((group) => group.fields)
		.find((field) => field.label === label);

describe('transactionDetailGroups', () => {
	it('always states what identifies the movement', () => {
		const t = txn();
		// The provider prefix is dropped for display, exactly as the ledger's Reference column does.
		expect(find(t, 'Reference')).toMatchObject({ value: 'AB12345', mono: true });
		expect(find(t, 'Date')?.value).toBe('27 Jul 2026');
		expect(find(t, 'Type')?.value).toBe('Payment');
	});

	it('calls a cash movement’s asset Cash, as the Asset column does, with no ISIN or units', () => {
		const t = txn();
		expect(find(t, 'Asset')?.value).toBe('Cash');
		expect(find(t, 'ISIN')).toBeUndefined();
		expect(find(t, 'Units')).toBeUndefined();
	});

	// Everything the row shows is in the drawer too: the account's id sits under its name on the
	// row, and the adviser is what the search and the filter match on.
	it('states the account id and the adviser, and says when nobody is assigned', () => {
		const t = txn({ account_uid: 'seccl_AX1' });
		expect(find(t, 'Account ID')).toMatchObject({ mono: true });
		expect(find(t, 'Account ID')?.value).toBeTruthy();
		// `person` is what draws the face beside the name, as the adviser columns do.
		expect(find(t, 'Adviser')).toMatchObject({ value: 'Mark Alford', person: 'Mark Alford' });
		expect(find(txn({ adviser: null }), 'Adviser')).toEqual({
			label: 'Adviser',
			value: 'Unassigned'
		});
	});

	it('marks the currency so the drawer can draw its flag', () => {
		expect(find(txn({ currency: 'USD' }), 'Currency')).toMatchObject({
			value: 'USD',
			currency: 'USD'
		});
	});

	it('shows the asset, its ISIN and the units on an order', () => {
		const t = txn({
			type: 'order',
			asset: 'Vanguard FTSE All-World',
			isin: 'IE00B3RBWM25',
			quantity: 1234.5
		});
		expect(find(t, 'Asset')?.value).toBe('Vanguard FTSE All-World');
		expect(find(t, 'ISIN')).toMatchObject({ value: 'IE00B3RBWM25', mono: true });
		expect(find(t, 'Units')?.value).toBe('1,234.5');
	});

	// Zero units is a real answer (a cancelled order), and `if (!quantity)` would have hidden it.
	it('states a zero unit count rather than treating it as absent', () => {
		expect(find(txn({ type: 'order', quantity: 0 }), 'Units')?.value).toBe('0');
	});

	it('carries the custodian’s own word only when it says more than the type', () => {
		expect(find(txn({ subtype: 'Cash receipt' }), 'Detail')?.value).toBe('Cash receipt');
		// "Payment" against type `payment` is the same statement twice — the Type column applies
		// this same test, so the drawer and the row cannot disagree.
		expect(find(txn({ subtype: 'payment' }), 'Detail')).toBeUndefined();
	});

	it('omits a method, a currency and a category the row does not carry', () => {
		const bare = txn({ method: null, currency: null });
		expect(find(bare, 'Method')).toBeUndefined();
		expect(find(bare, 'Currency')).toBeUndefined();
		expect(find(bare, 'Category')).toBeUndefined();
		expect(find(txn({ category: 'transfer_in' }), 'Category')?.value).toBe('Transfer in');
	});

	it('leaves the amount and the status to the drawer’s heading', () => {
		const labels = transactionDetailGroups(txn())
			.flatMap((group) => group.fields)
			.map((field) => field.label);
		expect(labels).not.toContain('Amount');
		expect(labels).not.toContain('Status');
	});

	it('drops a group that has nothing left in it', () => {
		const titles = transactionDetailGroups(txn({ record: {} })).map((group) => group.title);
		expect(titles).toEqual(['The movement', 'The investment', 'Where it sits']);
	});

	it('states the charge split when it says more than the total', () => {
		const t = txn({
			type: 'order',
			currency: 'GBP',
			fee_amount: 6.5,
			dealing_charges: 4.5,
			other_charges: 2,
			fee_type: 'Dealing + Other Charge',
			trade_date: '2026-07-01',
			realised_gain: 200
		});
		expect(find(t, 'Fee')?.value).toBe('£6.50');
		expect(find(t, 'Dealing')?.value).toBe('£4.50');
		expect(find(t, 'Other')?.value).toBe('£2.00');
		expect(find(t, 'Fee type')?.value).toBe('Dealing + Other Charge');
		expect(find(t, 'Traded')?.value).toBe('1 Jul 2026');
		expect(find(t, 'Realised gain')?.value).toBe('£200.00');
		// Money fields also carry the figure, so the drawer can split pounds from pence.
		expect(find(t, 'Fee')?.money).toEqual({ amount: 6.5, currency: 'GBP' });
		expect(find(t, 'Realised gain')?.money).toEqual({ amount: 200, currency: 'GBP' });
		expect(find(t, 'Fee type')?.money).toBeUndefined();
	});

	it('states the dealing price, the fee and the tax on a trade', () => {
		const t = txn({
			type: 'order',
			subtype: 'Buy',
			asset: 'Barclays PLC',
			quantity: 2400,
			currency: 'GBP',
			amount: -12439.68,
			price: 5.1832,
			fee_amount: -81.86,
			tax_amount: -1.5,
			settlement_amount: -12521.54
		});
		expect(find(t, 'Price')?.value).toBe('£5.1832');
		expect(find(t, 'Units')?.value).toBe('2,400');
		expect(find(t, 'Fee')?.value).toBe('-£81.86');
		expect(find(t, 'Tax')?.value).toBe('-£1.50');
		expect(find(t, 'Settlement')?.value).toBe('-£12,521.54');
	});

	it('does not call a cashless transfer a zero settlement', () => {
		const t = txn({ type: 'transfer', amount: 3763.2, settlement_amount: 0 });
		expect(find(t, 'Settlement')).toBeUndefined();
	});

	it('leaves a cash movement without a price, a fee or a tax', () => {
		const labels = transactionDetailGroups(txn()).flatMap((group) =>
			group.fields.map((field) => field.label)
		);
		expect(labels).not.toContain('Price');
		expect(labels).not.toContain('Fee');
		expect(labels).not.toContain('Tax');
		expect(labels).not.toContain('Settlement');
	});

	it('does not repeat the fee under the custodian’s own column name', () => {
		const t = txn({
			fee_amount: -5.42,
			record: { NET_FEE_AMT: '5.42', NARRATIVE: 'Sale' }
		});
		expect(find(t, 'Fee')?.value).toBe('-£5.42');
		expect(find(t, 'Net fee amt')).toBeUndefined();
		expect(find(t, 'Narrative')?.value).toBe('Sale');
	});

	it('states the settlement date and the custodian when the row carries them', () => {
		const t = txn({ settlement_date: '2026-08-28', custodian: 'sei' });
		expect(find(t, 'Settlement date')?.value).toBe('28 Aug 2026');
		expect(find(t, 'Custodian')?.value).toBe('SEI');
	});

	it('lists every populated custodian field, and nothing the payload left blank', () => {
		const t = txn({
			record: {
				transaction_description: 'Cash Receipt',
				BASE_EXCHANGE_RATE: '0.75500189',
				transaction_id: '384801834',
				trade_date: '2026-08-27T00:00:00'
			}
		});
		const group = transactionDetailGroups(t).find(
			(item) => item.title === 'Recorded by the custodian'
		);
		expect(group?.fields.map((field) => field.label)).toEqual([
			'Base exchange rate',
			'Trade date',
			'Transaction description',
			'Transaction id'
		]);
		expect(find(t, 'Base exchange rate')?.value).toBe('0.75500189');
		expect(find(t, 'Transaction id')).toMatchObject({ value: '384801834', mono: true });
		expect(find(t, 'Trade date')?.value).toBe('27 Aug 2026');
		expect(find(t, 'Amount')).toBeUndefined();
	});
});

describe('conversionCaption', () => {
	it('states the same figure in the account currency', () => {
		expect(
			conversionCaption({
				amount: 1_000_000_000,
				currency: 'EUR',
				base_amount: 857_000_000,
				base_currency: 'GBP'
			})
		).toMatchObject({ amount: 857_000_000, currency: 'GBP' });
		expect(
			String(
				conversionCaption({
					amount: 1_000_000_000,
					currency: 'EUR',
					base_amount: 857_000_000,
					base_currency: 'GBP'
				})
			)
		).toBe('£857,000,000.00');
	});

	it('stays quiet when the movement is already in the account currency', () => {
		expect(
			conversionCaption({
				amount: 1_000,
				currency: 'GBP',
				base_amount: 1_000,
				base_currency: 'gbp'
			})
		).toBeNull();
	});

	it('stays quiet when there is no rate, or nothing to convert', () => {
		expect(
			conversionCaption({ amount: 1_000, currency: 'USD', base_amount: null, base_currency: 'GBP' })
		).toBeNull();
		expect(
			conversionCaption({ amount: 0, currency: 'USD', base_amount: 0, base_currency: 'GBP' })
		).toBeNull();
	});
});
