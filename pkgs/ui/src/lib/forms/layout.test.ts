import { describe, expect, it } from 'vitest';
import { isContributionProduct, layoutFields, presentField } from './layout.js';
import type { FormField } from './types.js';

function field(key: string, column?: 'left' | 'right', over: Partial<FormField> = {}): FormField {
	return {
		uid: key,
		fieldKey: key,
		fieldType: 'text',
		label: key,
		section: 'Funding',
		description: null,
		required: false,
		conditions: null,
		validation: column ? { column } : null,
		options: null,
		sortOrder: 0,
		...over
	};
}

describe('layoutFields', () => {
	it('keeps an option in the left column and the fields it reveals on the right', () => {
		const rows = layoutFields([
			field('has_transfer', 'left'),
			field('transfer_type', 'right'),
			field('transfer_extent', 'right'),
			field('has_contribution', 'left'),
			field('contribution_amount', 'right')
		]);
		expect(rows).toEqual([
			{
				kind: 'pair',
				option: expect.objectContaining({ fieldKey: 'has_transfer' }),
				details: [
					expect.objectContaining({ fieldKey: 'transfer_type' }),
					expect.objectContaining({ fieldKey: 'transfer_extent' })
				]
			},
			{
				kind: 'pair',
				option: expect.objectContaining({ fieldKey: 'has_contribution' }),
				details: [expect.objectContaining({ fieldKey: 'contribution_amount' })]
			}
		]);
	});

	it('leaves an unticked option as a row with nothing beside it', () => {
		const rows = layoutFields([field('has_transfer', 'left'), field('currency')]);
		expect(rows[0]).toMatchObject({ kind: 'pair', details: [] });
		expect(rows[1]).toMatchObject({ kind: 'grid' });
	});
});

describe('presentField', () => {
	const payment = field('has_contribution', 'left', {
		label: 'Expect a payment in',
		validation: { column: 'left', contributionLabel: 'Expect a contribution' }
	});

	it('says contribution on a pension or a JISA', () => {
		expect(isContributionProduct('SIPP')).toBe(true);
		expect(isContributionProduct('jisa')).toBe(true);
		expect(isContributionProduct('GIA')).toBe(false);
		expect(presentField(payment, { account: { product_type: 'SIPP' } }).label).toBe(
			'Expect a contribution'
		);
		expect(presentField(payment, { account: { product_type: 'GIA' } }).label).toBe(
			'Expect a payment in'
		);
	});

	it('offers Pay away only for Epworth, and keeps a pay-away answer that is already stored', () => {
		const dividend = field('dividend_treatment', undefined, {
			options: {
				options: [
					{ value: 'reinvest', label: 'Reinvest' },
					{ value: 'cash', label: 'Cash' },
					{ value: 'pay_away', label: 'Pay away' }
				]
			}
		});
		const sei = presentField(dividend, { account: { custodian: 'sei' } });
		const listed = (sei.options as { options: { value: string }[] }).options.map((o) => o.value);
		expect(listed).toEqual(['reinvest', 'cash']);
		const kept = presentField(
			dividend,
			{ account: { custodian: 'sei' } },
			{
				dividend_treatment: 'pay_away'
			}
		);
		expect(
			(kept.options as { options: { value: string }[] }).options.map((o) => o.value)
		).toContain('pay_away');
		const epworth = presentField(dividend, { account: { custodian: 'Epworth' } });
		expect(
			(epworth.options as { options: { value: string }[] }).options.map((o) => o.value)
		).toContain('pay_away');
	});
});
