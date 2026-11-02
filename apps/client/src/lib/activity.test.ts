import { describe, expect, it } from 'vitest';
import type { Transaction } from '$lib/api';
import {
	activityDetail,
	activityType,
	instrumentName,
	movementDetail,
	signedAmount,
	toActivity
} from './activity';

const tx = (t: Partial<Transaction>): Transaction => t as Transaction;

describe('movementDetail', () => {
	// The defect: the account record printed the custodian's key exactly as it arrived, so a
	// client read "sell" and "transfer_in" in their own ledger.
	it('prints the canonical type as words, not raw', () => {
		expect(movementDetail(tx({ type: 'sell' }))).toBe('Sell');
		expect(movementDetail(tx({ type: 'transfer_in' }))).toBe('Transfer in');
	});

	it('names the custodian subtype GET /crm/transactions actually returns', () => {
		expect(movementDetail(tx({ type: 'transfer_in', subtype: 'cash_transfer' }))).toBe(
			'Transfer in · Cash transfer'
		);
	});

	it('does not repeat the type when the listing coalesced subtype back onto it', () => {
		expect(movementDetail(tx({ type: 'sell', subtype: 'sell' }))).toBe('Sell');
	});

	it('drops an absent segment rather than printing a dangling separator', () => {
		expect(movementDetail(tx({ type: 'buy', subtype: null }))).toBe('Buy');
		expect(movementDetail(tx({}))).toBe('');
	});

	// Only the first letter is touched, so a custodian's acronym survives (sentence case, never
	// title case — `class="capitalize"` would give "Isa Subscription").
	it('leaves an acronym alone', () => {
		expect(movementDetail(tx({ type: 'ISA subscription' }))).toBe('ISA subscription');
	});
});

describe('instrumentName', () => {
	it('reads `asset`, the listing column, not the lake-shaped alias', () => {
		expect(instrumentName(tx({ asset: 'Global Equity Fund', type: 'sell' }))).toBe(
			'Global Equity Fund'
		);
		expect(instrumentName(tx({ type: 'sell' }))).toBeNull();
	});
});

describe('signedAmount / activityType / toActivity', () => {
	it('still reads money out as negative and buckets the icon kind', () => {
		expect(signedAmount(tx({ amount: 250, type: 'sell' }))).toBe(-250);
		expect(signedAmount(tx({ amount: 250, type: 'transfer_in' }))).toBe(250);
		expect(signedAmount(tx({ amount: 250, type: 'transfer_out' }))).toBe(-250);
		// A pending units-denominated sell has no amount yet — £0.00 would claim it moved nothing.
		expect(signedAmount(tx({ amount: null, type: 'sell' }))).toBeNull();
		expect(signedAmount(tx({ amount: 250, type: 'sell', signed_amount: -250 }))).toBe(-250);
		expect(activityType(tx({ subtype: 'Redemption' }))).toBe('sell');
	});

	// `type` from /crm/transactions IS `platform.transaction_type`. Interest used to fall through
	// the needle search to 'buy', so a client read "+£0.01 Buy" against their cash.
	it('names a canonical kind as itself rather than bucketing it into a neighbour', () => {
		expect(activityType(tx({ type: 'interest', subtype: '20043' }))).toBe('interest');
		expect(activityType(tx({ type: 'transfer_in', subtype: 'In Specie' }))).toBe('transfer_in');
		expect(activityType(tx({ type: 'transfer_out', subtype: 'Internal' }))).toBe('transfer_out');
		expect(activityType(tx({ type: 'corporate_action' }))).toBe('corporate_action');
		expect(toActivity(tx({ type: 'interest', amount: 0.01 })).description).toBe('Interest');
	});

	it('describes the composed activity row from the live asset key', () => {
		expect(toActivity(tx({ asset: 'Fund A', type: 'buy' })).description).toBe('Fund A');
	});

	// A cash movement names no instrument, so the fallback IS the title for every payment, fee
	// and dividend on the feed. It was the raw key, so a payment read "deposit" sitting directly
	// above a subtitle that said "Payment" — one row, two words for one thing.
	it('titles a cash movement with the same word its own subtitle uses', () => {
		expect(toActivity(tx({ type: 'deposit', amount: 355 })).description).toBe('Payment');
		expect(toActivity(tx({ type: 'fee', amount: -406 })).description).toBe('Fee');
		expect(toActivity(tx({ type: 'dividend', amount: 389 })).description).toBe('Dividend');
	});

	// The title and the subtitle are the SAME derivation now, so they cannot disagree — which is
	// the point: the row used to print the custodian's key as the title and the bucket's word
	// underneath it.
	it('titles by the bucket, so an unmapped custodian word is never printed raw', () => {
		const row = toActivity(tx({ type: 'Mystery movement', amount: 440 }));
		expect(row.description).toBe('Buy');
		expect(row.type).toBe('buy');
	});

	it('carries the detail, units, price and ISIN through for a wide feed', () => {
		const row = toActivity(
			tx({
				type: 'buy',
				subtype: 'At Best',
				asset: 'Fund A',
				isin: 'GB00B3X7QG63',
				quantity: 416,
				price: 2.9675,
				amount: 1234.5
			})
		);
		expect(row).toMatchObject({
			detail: 'At Best',
			quantity: 416,
			price: 2.9675,
			isin: 'GB00B3X7QG63'
		});
		expect(toActivity(tx({ type: 'deposit', amount: 10 }))).toMatchObject({
			detail: null,
			quantity: null,
			price: null,
			isin: null
		});
	});
});

describe('activityDetail', () => {
	it('is the custodian’s words when they say more than the kind', () => {
		expect(activityDetail(tx({ type: 'fee', subtype: 'Platform Fee' }))).toBe('Platform Fee');
		expect(activityDetail(tx({ type: 'deposit', subtype: 'Employer' }))).toBe('Employer');
		expect(activityDetail(tx({ type: 'dividend', subtype: 'Accumulation Dividend' }))).toBe(
			'Accumulation Dividend'
		);
	});

	it('is nothing when the subtype only echoes the kind, or is a provider code', () => {
		// The listing coalesces an absent subtype back onto the type.
		expect(activityDetail(tx({ type: 'deposit', subtype: 'deposit' }))).toBeNull();
		expect(activityDetail(tx({ type: 'deposit', subtype: 'Payment' }))).toBeNull();
		expect(activityDetail(tx({ type: 'transfer_in', subtype: 'transfer_in' }))).toBeNull();
		expect(activityDetail(tx({ type: 'interest', subtype: '20043' }))).toBeNull();
		expect(activityDetail(tx({ type: 'fee', subtype: '' }))).toBeNull();
		expect(activityDetail(tx({ type: 'fee' }))).toBeNull();
	});

	it('still says Transaction when the movement has no type at all', () => {
		expect(toActivity(tx({ amount: 10 })).description).toBe('Transaction');
	});

	it('carries the movement currency through so the amount is not forced to GBP', () => {
		expect(toActivity(tx({ amount: 10, currency: 'USD' })).currency).toBe('USD');
		expect(toActivity(tx({ amount: 10 })).currency).toBe('GBP');
	});
});
