import { describe, expect, it } from 'vitest';
import { ACTIVITY_LABELS, activityDate, activityLabel, activityUnits } from './activity.js';

/**
 * The one vocabulary every activity look reads. It was two copies (list and table), which is
 * how a third look becomes the moment the portal starts calling one movement by two names.
 */
describe('activityLabel', () => {
	it('names money in the way the adviser platform names it', () => {
		// "Payment" is CASH_INSTRUCTION_TITLE.expected_in — the word on the button an adviser
		// presses to raise one. A client told "deposit" is reading a different firm.
		expect(activityLabel('deposit')).toBe('Payment');
	});

	it('names the other canonical kinds', () => {
		expect(activityLabel('withdrawal')).toBe('Withdrawal');
		expect(activityLabel('dividend')).toBe('Dividend');
		expect(activityLabel('interest')).toBe('Interest');
		expect(activityLabel('fee')).toBe('Fee');
		expect(activityLabel('tax')).toBe('Tax');
		expect(activityLabel('buy')).toBe('Buy');
		expect(activityLabel('sell')).toBe('Sell');
		expect(activityLabel('transfer_in')).toBe('Transfer in');
		expect(activityLabel('transfer_out')).toBe('Transfer out');
		expect(activityLabel('corporate_action')).toBe('Corporate action');
		expect(activityLabel('adjustment')).toBe('Adjustment');
	});

	// The rule that matters beyond the enum: a slug is never printed as a slug.
	it('sentence-cases anything it does not name, rather than printing the key', () => {
		expect(activityLabel('stock_lending')).toBe('Stock lending');
		expect(activityLabel('corporate-action')).toBe('Corporate action');
	});

	it('leaves an acronym alone — sentence case, never title case', () => {
		expect(activityLabel('ISA subscription')).toBe('ISA subscription');
	});

	// `platform.transaction_type`, the enum every provider's rows are conformed to. A kind the
	// column can hold but this map cannot name is a kind the feed prints as its neighbour — which
	// is how interest read as "Buy".
	it('is total over the transaction type enum', () => {
		expect(Object.keys(ACTIVITY_LABELS).sort()).toEqual([
			'adjustment',
			'buy',
			'corporate_action',
			'deposit',
			'dividend',
			'fee',
			'interest',
			'sell',
			'tax',
			'transfer_in',
			'transfer_out',
			'withdrawal'
		]);
	});
});

describe('activityUnits', () => {
	it('says the units and the price a trade dealt at', () => {
		expect(activityUnits(416, 1.2345)).toBe('416 units @ £1.2345');
		expect(activityUnits(12.5, 2, 'USD')).toBe('12.5 units @ US$2.00');
	});

	it('says just the units when the custodian priced nothing', () => {
		expect(activityUnits(100, null)).toBe('100 units');
		expect(activityUnits(100, 0)).toBe('100 units');
	});

	it('is nothing for a cash movement, so a wide feed leaves the cell blank', () => {
		expect(activityUnits(null, null)).toBeNull();
		expect(activityUnits(undefined, 3)).toBeNull();
		expect(activityUnits(0, 3)).toBeNull();
	});

	it('reads a sale’s negative quantity as a count', () => {
		expect(activityUnits(-250, 1)).toBe('250 units @ £1.00');
	});
});

/**
 * The feed's date is printed as words, whichever shape the API or a fixture handed over. A real
 * book's feed was showing `2025-09-10T00:00:00` as a group heading: the API's `str(row["date"])`
 * of a timestamp, printed as it came by all three looks.
 */
describe('activityDate', () => {
	it('formats a date-only ISO stamp', () => {
		expect(activityDate('2025-09-10')).toBe('10 Sept 2025');
	});

	it('formats a timestamp the same way — the time is never shown on the feed', () => {
		expect(activityDate('2025-09-10T00:00:00')).toBe('10 Sept 2025');
		expect(activityDate('2025-09-10 00:00:00')).toBe('10 Sept 2025');
	});

	it('passes an already-printed date through untouched', () => {
		expect(activityDate('14 Jun 2026')).toBe('14 Jun 2026');
	});

	it('prints nothing for nothing', () => {
		expect(activityDate('')).toBe('');
		expect(activityDate(null)).toBe('');
		expect(activityDate(undefined)).toBe('');
	});
});
