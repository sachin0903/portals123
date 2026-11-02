/**
 * The client's standing arrangements — the flattening, the ordering and the three labels.
 *
 * Pure, and tested here rather than through the panel, because every one of these is a statement
 * about what a reader is told: which way the money goes, when, and whether a rule applies at all.
 */
import { describe, expect, it } from 'vitest';
import {
	autoInvestLabel,
	ordinal,
	scheduleLabel,
	standingRows,
	type StandingAccount
} from './standing';

const isa: StandingAccount = {
	uid: '02569C7',
	name: "Bruce's ISA",
	withdrawals: [{ amount: 100, payment_day: 14, method: 'BACS Credit', auto_disinvest: 'Largest' }],
	payments: null
};
const sipp: StandingAccount = {
	uid: '0311AB2',
	name: "Bruce's SIPP",
	payments: [{ amount: 250, payment_day: 1, method: 'Direct Debit' }],
	withdrawals: []
};

describe('standingRows', () => {
	it('flattens every account into one list, naming the account on each row', () => {
		// The whole point of the panel: an adviser asked "what is going in and out for this person?"
		// should not have to open four accounts to answer.
		const rows = standingRows([isa, sipp]);
		expect(rows.map((r) => [r.accountName, r.direction, r.amount])).toEqual([
			["Bruce's ISA", 'out', 100],
			["Bruce's SIPP", 'in', 250]
		]);
	});

	it('puts money going OUT first', () => {
		// A withdrawal that will not be covered is the thing that goes wrong, and it is what the client
		// asks about — so the order is by consequence, not alphabetical by a label nobody reads.
		const rows = standingRows([sipp, isa]);
		expect(rows[0].direction).toBe('out');
	});

	it('orders within a direction by account, then by the day of the month', () => {
		const busy: StandingAccount = {
			uid: 'A1',
			name: 'Alpha',
			withdrawals: [
				{ amount: 10, payment_day: 28 },
				{ amount: 20, payment_day: 3 }
			]
		};
		const rows = standingRows([busy]);
		expect(rows.map((r) => r.payment_day)).toEqual([3, 28]);
	});

	it('gives every row a stable key without a provider id', () => {
		// Seccl's recurring payments are an UNKEYED array, so there is no id to use and position within
		// the account is the identity. Two rows on one account must still be distinguishable, or the
		// table's keyed each would collapse them.
		const twin: StandingAccount = {
			uid: 'A1',
			withdrawals: [
				{ amount: 10, payment_day: 5 },
				{ amount: 10, payment_day: 5 }
			]
		};
		const keys = standingRows([twin]).map((r) => r.key);
		expect(new Set(keys).size).toBe(2);
	});

	it('is empty for a client whose accounts have no arrangements', () => {
		// The commonest case, and the panel shows an empty state rather than a heading over nothing.
		expect(standingRows([{ uid: 'A1' }, { uid: 'A2', payments: [], withdrawals: [] }])).toEqual([]);
	});

	it('adds no rows for a value that is not a list', () => {
		// Onboarding stored an OBJECT in these jsonb columns; iterating it stopped the whole record.
		const onboarded = {
			uid: 'A1',
			payments: { regular_payments: { amount: 250 } },
			withdrawals: { amount: 100, frequency: 'monthly' }
		} as unknown as StandingAccount;
		expect(standingRows([onboarded])).toEqual([]);
	});

	it('falls back to the uid when an account has no name', () => {
		// A real state on a book mid-migration; "undefined" in the Account column is worse than the id.
		expect(standingRows([{ uid: '02569C7', withdrawals: [{ amount: 1 }] }])[0].accountName).toBe(
			'02569C7'
		);
	});
});

describe('scheduleLabel', () => {
	it('reads as one sentence, from two sources', () => {
		// The DAY is the provider's and the CADENCE is the conformed layer's (Monthly, because a
		// `paymentDay` is monthly by construction). Neither is sufficient alone.
		expect(scheduleLabel({ payment_day: 14, frequency: 'Monthly' })).toBe('14th of every month');
	});

	it('states the cadence alone when the provider did not say when', () => {
		// A schedule with no day is still a schedule.
		expect(scheduleLabel({ payment_day: null, frequency: 'Monthly' })).toBe('Monthly');
		expect(scheduleLabel({})).toBe('Monthly');
	});
});

describe('ordinal', () => {
	it('gets the ordinary cases right', () => {
		expect([1, 2, 3, 4, 21, 22, 23].map(ordinal)).toEqual([
			'1st',
			'2nd',
			'3rd',
			'4th',
			'21st',
			'22nd',
			'23rd'
		]);
	});

	it('gets the TEENS right, which is the case every naive version misses', () => {
		// "the 11st of every month" is a date a client reads and does not recognise.
		expect([11, 12, 13].map(ordinal)).toEqual(['11th', '12th', '13th']);
	});
});

describe('autoInvestLabel', () => {
	it('says N/A on money coming in', () => {
		// The rule is about RAISING cash to pay a withdrawal, so it does not apply to an inbound
		// payment. "N/A" is the honest answer where a blank cell would read as "we do not know".
		expect(autoInvestLabel({ direction: 'in', auto_disinvest: null })).toBe('N/A');
		// ...even if the provider happened to state one, because it still does not apply.
		expect(autoInvestLabel({ direction: 'in', auto_disinvest: 'Largest' })).toBe('N/A');
	});

	it('states the rule on money going out, and says so when there is none', () => {
		expect(autoInvestLabel({ direction: 'out', auto_disinvest: 'Largest' })).toBe('Largest');
		// "Not set" rather than blank: an outbound arrangement with no disinvestment rule is a
		// withdrawal that may not be funded, which is worth reading as a gap.
		expect(autoInvestLabel({ direction: 'out', auto_disinvest: null })).toBe('Not set');
	});
});
