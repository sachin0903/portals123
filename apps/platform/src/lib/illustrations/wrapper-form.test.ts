import { describe, expect, it } from 'vitest';
import {
	WRAPPER_SECTIONS,
	toWrapperIllustrationPayload,
	wrapperIllustrationFields
} from './wrapper-form';

describe('wrapperIllustrationFields', () => {
	it('uses the same plan width as the pension form, two fields to a row', () => {
		const plan = wrapperIllustrationFields('ISA', false).filter((f) => f.section === 'Plan');
		for (const f of plan) expect(f.validation?.width, f.fieldKey).toBe(6);
	});

	it('asks for a platform fee only when the firm has more than one', () => {
		expect(wrapperIllustrationFields('GIA', false).map((f) => f.fieldKey)).not.toContain(
			'fee_package_id'
		);
		const fee = wrapperIllustrationFields('GIA', true).find((f) => f.fieldKey === 'fee_package_id');
		expect(fee?.fieldType).toBe('select');
		expect(fee?.required).toBe(true);
	});

	it('describes a joint owner as a second person on a GIA', () => {
		expect(WRAPPER_SECTIONS['Joint owner']?.description).toBe('A second person named on a GIA.');
	});

	it('asks for a joint owner on a GIA and not on an ISA', () => {
		expect(wrapperIllustrationFields('GIA', false).map((f) => f.fieldKey)).toEqual(
			expect.arrayContaining(['joint_owner_name', 'joint_owner_dob'])
		);
		expect(wrapperIllustrationFields('ISA', false).map((f) => f.fieldKey)).not.toContain(
			'joint_owner_name'
		);
	});

	it('picks the model from the same searchable list as a pension illustration', () => {
		const model = wrapperIllustrationFields('GIA', false).find((f) => f.fieldKey === 'model_id');
		expect(model?.fieldType).toBe('combobox');
		expect(model?.options).toEqual({ source: 'models', valueKey: 'id', labelKey: 'name' });
	});
});

describe('toWrapperIllustrationPayload', () => {
	it('sends adviser percents as fractions and uses the only platform fee', () => {
		const body = toWrapperIllustrationPayload(
			{
				model_id: '4',
				existing_balance: '1000',
				adviser_ongoing: '0.5',
				start_date: '2026-10-01',
				term_years: 8
			},
			'ISA',
			12
		);
		expect(body.wrapper).toBe('ISA');
		expect(body.adviser_ongoing).toBe('0.005000');
		expect(body.fee_package_id).toBe(12);
		expect(body.model_id).toBe(4);
		expect(body.term_years).toBe(8);
		expect(body.joint_owner_name).toBeNull();
		expect(body.contributions).toEqual([]);
	});

	it('includes a regular contribution only when that section was collected', () => {
		const body = toWrapperIllustrationPayload(
			{
				regular_amount: '250',
				regular_frequency: 'quarterly',
				regular_start: '2026-10-01',
				escalation: '2'
			},
			'GIA',
			null
		);
		expect(body.contributions).toEqual([
			{
				type: 'regular',
				amount: '250',
				start: '2026-10-01',
				end: null,
				frequency: 'quarterly',
				escalation: '0.020000'
			}
		]);
	});

	it('prefers the fee the adviser picked', () => {
		const body = toWrapperIllustrationPayload({ fee_package_id: '9' }, 'GIA', 12);
		expect(body.fee_package_id).toBe(9);
	});
});
