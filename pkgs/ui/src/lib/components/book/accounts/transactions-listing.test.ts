import { describe, expect, it } from 'vitest';
import { transactionFilterDefs } from './transactions-listing.svelte';

const option = [{ value: 'a', label: 'A' }];

describe('transactionFilterDefs', () => {
	it('preloads advisers, keeps currency type-to-search, and offers custodian', () => {
		const defs = transactionFilterDefs({
			showAdviserFilter: true,
			showAccountFilter: false,
			statusOptions: option,
			typeOptions: option,
			adviserOptions: option,
			accountOptions: option,
			currencyOptions: option,
			custodianOptions: [{ value: 'seccl', label: 'Seccl' }]
		});
		const byKey = Object.fromEntries(defs.map((def) => [def.key, def]));
		expect(byKey.adviser?.autocomplete).toBeUndefined();
		expect(byKey.currency?.autocomplete).toBe(true);
		expect(byKey.custodian?.label).toBe('Custodian');
		expect(byKey.custodian?.options).toEqual([{ value: 'seccl', label: 'Seccl' }]);
		expect(byKey.status?.autocomplete).toBeUndefined();
		expect(byKey.custodian?.autocomplete).toBeUndefined();
	});

	it('still offers custodian when the ledger is already one client', () => {
		const defs = transactionFilterDefs({
			showAdviserFilter: false,
			showAccountFilter: true,
			statusOptions: option,
			typeOptions: option,
			adviserOptions: option,
			accountOptions: option,
			currencyOptions: option,
			custodianOptions: option
		});
		expect(defs.map((def) => def.key)).toEqual([
			'status',
			'type',
			'account',
			'custodian',
			'currency',
			'amount',
			'quantity'
		]);
	});
});
