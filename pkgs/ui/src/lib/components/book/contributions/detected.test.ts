import { describe, expect, it } from 'vitest';
import { creditAsContribution, detectedOnPage, resolveFocus, workQueue } from './detected.js';
import type { Contribution, UnclassifiedCredit } from './types.js';
import { emptyQuery, type ListingQuery } from '../listing/query.js';

const credit = (over: Partial<UnclassifiedCredit> = {}): UnclassifiedCredit => ({
	id: 501,
	source_system: 'sei',
	source_transaction_id: '368445711',
	type: 'deposit',
	source_type: 'Cash Receipt',
	source_subtype: '21121',
	amount: 800,
	currency: 'GBP',
	transaction_date: '2026-06-03',
	settlement_date: null,
	status: null,
	account_id: 10,
	account_uid: 'acc_1',
	account_name: "Sophie's SIPP",
	product_type: 'SIPP',
	contact_id: 5,
	contact_uid: 'ct_1',
	client_name: 'Sophie Halloway',
	adviser: 'Dan Reeve',
	client_employer_name: null,
	is_relief_payment: false,
	suggested_category: 'personal_contribution',
	provider_description: null,
	method: null,
	...over
});

const booked = (uid: string): Contribution =>
	({ ...creditAsContribution(credit({ id: 9 })), uid, status: 'confirmed' }) as Contribution;

const listing = (over: Partial<ListingQuery> = {}): ListingQuery => ({
	...emptyQuery([], 25),
	...over
});

describe('resolveFocus', () => {
	it('translates a transaction uid from an Action required strip to its detected row', () => {
		// The strips link `{source_system}:{source_transaction_id}`; the queue keys the same
		// payment `credit:{id}`. Unmatched, "Categorise contribution" opened nothing.
		const credits = [credit()];
		const rows = workQueue([], credits);
		expect(resolveFocus('sei:368445711', rows, credits)).toBe('credit:501');
	});

	it('leaves a uid that already names a row alone — a booked contribution, or a credit key', () => {
		const credits = [credit()];
		const rows = workQueue([booked('con_1')], credits);
		expect(resolveFocus('con_1', rows, credits)).toBe('con_1');
		expect(resolveFocus('credit:501', rows, credits)).toBe('credit:501');
	});

	it('passes an unknown focus through, and nothing for none', () => {
		expect(resolveFocus('con_gone', [], [])).toBe('con_gone');
		expect(resolveFocus(null, [], [])).toBeNull();
	});
});

describe('detectedOnPage', () => {
	const rows = workQueue(
		[booked('con_1')],
		[credit(), credit({ id: 502, client_name: 'Omar Haddad', adviser: 'Priya Nair' })]
	);

	it('is every detected row on the first page, and none on a later one', () => {
		expect(detectedOnPage(rows, listing()).map((r) => r.uid)).toEqual(['credit:501', 'credit:502']);
		expect(detectedOnPage(rows, listing({ offset: 25 }))).toEqual([]);
	});

	it('narrows by the same search and filters the API applied to the booked rows', () => {
		expect(detectedOnPage(rows, listing({ q: 'omar' })).map((r) => r.uid)).toEqual(['credit:502']);
		expect(
			detectedOnPage(rows, listing({ filters: { adviser: ['Dan Reeve'] } })).map((r) => r.uid)
		).toEqual(['credit:501']);
		// A detected row is at the `identified` stage; a filter on another stage drops it.
		expect(detectedOnPage(rows, listing({ filters: { status: ['completed'] } }))).toEqual([]);
	});
});
