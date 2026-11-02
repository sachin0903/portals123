import { describe, expect, it } from 'vitest';
import { isEmptyValue, validateField } from './validate';

const base = { fieldType: 'text', label: 'Name', required: false, validation: null } as const;

describe('isEmptyValue', () => {
	it('treats null/undefined/blank/empty-array as empty', () => {
		expect(isEmptyValue(null)).toBe(true);
		expect(isEmptyValue(undefined)).toBe(true);
		expect(isEmptyValue('   ')).toBe(true);
		expect(isEmptyValue([])).toBe(true);
	});

	it('treats real values as non-empty', () => {
		expect(isEmptyValue('x')).toBe(false);
		expect(isEmptyValue(0)).toBe(false);
		expect(isEmptyValue(false)).toBe(false);
		expect(isEmptyValue(['a'])).toBe(false);
	});
});

describe('validateField', () => {
	it('passes an optional empty field', () => {
		expect(validateField(base, '')).toBeNull();
	});

	it('fails a required empty field with a labelled message', () => {
		expect(validateField({ ...base, required: true }, '')).toBe('Name is required');
	});

	it('enforces minLength / maxLength with a custom or default message', () => {
		const field = { ...base, validation: { minLength: 3, maxLength: 5 } };
		expect(validateField(field, 'ab')).toBe('Minimum 3 characters required');
		expect(validateField(field, 'abcdef')).toBe('Maximum 5 characters allowed');
		expect(validateField(field, 'abcd')).toBeNull();
	});

	it('uses errorMessage over the default when provided', () => {
		const field = { ...base, validation: { minLength: 3, errorMessage: 'Too short' } };
		expect(validateField(field, 'ab')).toBe('Too short');
	});

	it('enforces a regex pattern only for non-empty strings', () => {
		const field = { ...base, validation: { pattern: '^[A-Z]+$', patternMessage: 'Caps only' } };
		expect(validateField(field, 'ABC')).toBeNull();
		expect(validateField(field, 'abc')).toBe('Caps only');
	});

	it('never blocks on an invalid author-supplied pattern', () => {
		const field = { ...base, validation: { pattern: '[' } };
		expect(validateField(field, 'anything')).toBeNull();
	});

	it('validates number bounds', () => {
		const field = { ...base, fieldType: 'number', validation: { min: 1, max: 10 } };
		expect(validateField(field, 0)).toBe('Minimum value is 1');
		expect(validateField(field, 11)).toBe('Maximum value is 10');
		expect(validateField(field, 5)).toBeNull();
		expect(validateField(field, 'x')).toBe('Must be a number');
	});

	it('requires a row, then each required column, on a repeating group', () => {
		const field = {
			...base,
			fieldType: 'repeating',
			label: 'Pensions',
			required: true,
			options: [
				{ key: 'provider', label: 'Provider', type: 'text' },
				{ key: 'value', label: 'Value', type: 'currency', required: false }
			]
		};
		expect(validateField(field, [])).toBe('Pensions needs at least one row');
		expect(validateField(field, [{ provider: '', value: '' }])).toBe('Provider is required');
		expect(validateField(field, [{ provider: 'Aviva', value: '' }])).toBeNull();
		expect(validateField(field, [{ provider: 'Aviva', value: 0 }])).toBeNull();
	});

	it('lets an optional repeating group be empty, and still rejects a blank required column', () => {
		const field = {
			...base,
			fieldType: 'repeating',
			label: 'Dependants',
			required: false,
			options: [
				{ key: 'name', label: 'Name', type: 'text' },
				{ key: 'note', label: 'Note', type: 'text', required: false }
			]
		};
		expect(validateField(field, [])).toBeNull();
		expect(validateField(field, [{ name: '  ', note: '' }])).toBe('Name is required');
		expect(validateField(field, [{ name: 'Ada', note: '' }])).toBeNull();
	});

	it('requires the meaningful parts inside a composite address', () => {
		const field = { ...base, fieldType: 'address', label: 'Residential address', required: true };
		expect(validateField(field, { line1: '', city: '', postcode: '', country: 'GB' })).toBe(
			'Enter the address line, city, postal code and country'
		);
		expect(
			validateField(field, {
				line1: '123 Rue Brizeux',
				city: 'Plouvien',
				postcode: '29860',
				country: 'FR'
			})
		).toBeNull();
	});
});
