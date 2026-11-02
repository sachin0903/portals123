import { describe, it, expect } from 'vitest';
import type { FormField, FieldValidation } from '../types.js';
import { isDisplayField, pendingDefaults, computedValues, sectionAnchorId } from './compute';

function field(partial: Partial<FormField> & { fieldKey: string }): FormField {
	return {
		uid: `u-${partial.fieldKey}`,
		fieldType: 'text',
		label: partial.fieldKey,
		section: null,
		description: null,
		required: false,
		conditions: null,
		validation: null,
		options: null,
		sortOrder: 0,
		...partial
	};
}

const validation = (v: FieldValidation): FieldValidation => v;

describe('isDisplayField', () => {
	it('is true only for the display type', () => {
		expect(isDisplayField({ fieldType: 'display' })).toBe(true);
		expect(isDisplayField({ fieldType: 'text' })).toBe(false);
		expect(isDisplayField({ fieldType: 'currency' })).toBe(false);
	});
});

describe('pendingDefaults', () => {
	it('seeds a default when the value is undefined', () => {
		const fields = [field({ fieldKey: 'a', validation: validation({ defaultValue: 'Monthly' }) })];
		expect(pendingDefaults(fields, {})).toEqual({ a: 'Monthly' });
	});

	it('does not overwrite a defined answer (including empty string / false / 0)', () => {
		const fields = [field({ fieldKey: 'a', validation: validation({ defaultValue: 'Monthly' }) })];
		expect(pendingDefaults(fields, { a: '' })).toEqual({});
		expect(pendingDefaults(fields, { a: false })).toEqual({});
		expect(pendingDefaults(fields, { a: 0 })).toEqual({});
		expect(pendingDefaults(fields, { a: 'Annually' })).toEqual({});
	});

	it('ignores fields with no default', () => {
		const fields = [field({ fieldKey: 'a' }), field({ fieldKey: 'b', validation: validation({}) })];
		expect(pendingDefaults(fields, {})).toEqual({});
	});
});

describe('computedValues', () => {
	const accounts = [
		{ id: 12, product_type: 'SIPP', value: 25000 },
		{ id: 34, product_type: 'GIA', value: 5000 }
	];
	const fields = [
		field({
			fieldKey: 'wrapper',
			fieldType: 'display',
			validation: validation({
				computedFrom: { source: 'accounts', matchOn: 'account_id', valueKey: 'product_type' }
			})
		})
	];

	it('fills from the matched row, coercing the match to a string', () => {
		// account_id is a string (as a <select> would store it); the row id is a number.
		expect(computedValues(fields, { account_id: '12' }, { accounts })).toEqual({ wrapper: 'SIPP' });
		expect(computedValues(fields, { account_id: 34 }, { accounts })).toEqual({ wrapper: 'GIA' });
	});

	it('is undefined when nothing matches or the source is missing', () => {
		expect(computedValues(fields, { account_id: '' }, { accounts })).toEqual({
			wrapper: undefined
		});
		expect(computedValues(fields, { account_id: '99' }, { accounts })).toEqual({
			wrapper: undefined
		});
		expect(computedValues(fields, { account_id: '12' }, {})).toEqual({ wrapper: undefined });
	});

	it('honours a custom matchKey', () => {
		const byUid = [
			field({
				fieldKey: 'fund',
				validation: validation({
					computedFrom: { source: 'accounts', matchOn: 'acc', matchKey: 'ref', valueKey: 'value' }
				})
			})
		];
		const rows = [{ ref: 'ACC-1', value: 999 }];
		expect(computedValues(byUid, { acc: 'ACC-1' }, { accounts: rows })).toEqual({ fund: 999 });
	});

	it('ignores fields without computedFrom', () => {
		expect(computedValues([field({ fieldKey: 'a' })], {}, {})).toEqual({});
	});
});

describe('sectionAnchorId', () => {
	it('slugifies the section label', () => {
		expect(sectionAnchorId('Wrapper & fund')).toBe('form-section-wrapper-fund');
		expect(sectionAnchorId('Annuity basis')).toBe('form-section-annuity-basis');
	});

	it('falls back when the label has no alphanumerics', () => {
		expect(sectionAnchorId('—')).toBe('form-section-section');
	});
});
