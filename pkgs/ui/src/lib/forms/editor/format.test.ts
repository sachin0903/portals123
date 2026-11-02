import { describe, expect, it } from 'vitest';
import { formatFieldValue, formatDynamicFieldsForDisplay } from './format';

describe('formatFieldValue', () => {
	describe('null/undefined/empty handling', () => {
		it('returns empty string for null value', () => {
			expect(formatFieldValue({ value: null, field: { fieldType: 'text', options: null } })).toBe(
				''
			);
		});

		it('returns empty string for undefined value', () => {
			expect(
				formatFieldValue({ value: undefined, field: { fieldType: 'text', options: null } })
			).toBe('');
		});

		it('returns empty string for empty string value', () => {
			expect(formatFieldValue({ value: '', field: { fieldType: 'text', options: null } })).toBe('');
		});
	});

	describe('array values', () => {
		it('joins array values with comma', () => {
			expect(
				formatFieldValue({ value: ['a', 'b', 'c'], field: { fieldType: 'text', options: null } })
			).toBe('a, b, c');
		});

		it('resolves labels for multiSelect', () => {
			const result = formatFieldValue({
				value: ['opt1', 'opt2'],
				field: {
					fieldType: 'multiSelect',
					options: [
						{ value: 'opt1', label: 'Option 1' },
						{ value: 'opt2', label: 'Option 2' }
					]
				}
			});
			expect(result).toBe('Option 1, Option 2');
		});

		it('falls back to value when option not found in multiSelect', () => {
			const result = formatFieldValue({
				value: ['opt1', 'unknown'],
				field: { fieldType: 'multiSelect', options: [{ value: 'opt1', label: 'Option 1' }] }
			});
			expect(result).toBe('Option 1, unknown');
		});
	});

	describe('boolean/checkbox handling', () => {
		it('formats true as Yes for checkbox', () => {
			expect(
				formatFieldValue({ value: true, field: { fieldType: 'checkbox', options: null } })
			).toBe('Yes');
		});

		it('formats false as No for checkbox', () => {
			expect(
				formatFieldValue({ value: false, field: { fieldType: 'checkbox', options: null } })
			).toBe('No');
		});

		it('formats boolean true as Yes regardless of field type', () => {
			expect(formatFieldValue({ value: true, field: { fieldType: 'text', options: null } })).toBe(
				'Yes'
			);
		});

		it('formats boolean false as No regardless of field type', () => {
			expect(formatFieldValue({ value: false, field: { fieldType: 'text', options: null } })).toBe(
				'No'
			);
		});

		it("does not convert string 'false' to No", () => {
			expect(
				formatFieldValue({ value: 'false', field: { fieldType: 'text', options: null } })
			).toBe('false');
		});

		it("does not convert string 'true' to Yes", () => {
			expect(formatFieldValue({ value: 'true', field: { fieldType: 'text', options: null } })).toBe(
				'true'
			);
		});
	});

	describe('date formatting', () => {
		it('formats valid date string', () => {
			const result = formatFieldValue({
				value: '2024-01-15',
				field: { fieldType: 'date', options: null }
			});
			expect(result).toBe('15 Jan 2024');
		});

		it('formats valid dateOfBirth string', () => {
			const result = formatFieldValue({
				value: '1990-06-15',
				field: { fieldType: 'dateOfBirth', options: null }
			});
			expect(result).toBe('15 Jun 1990');
		});

		it('returns original value for invalid date', () => {
			expect(
				formatFieldValue({ value: 'not-a-date', field: { fieldType: 'date', options: null } })
			).toBe('not-a-date');
		});
	});

	describe('select/combobox with options', () => {
		it('returns label for matching select option', () => {
			const result = formatFieldValue({
				value: 'opt1',
				field: {
					fieldType: 'select',
					options: [
						{ value: 'opt1', label: 'Option 1' },
						{ value: 'opt2', label: 'Option 2' }
					]
				}
			});
			expect(result).toBe('Option 1');
		});

		it('returns value when no matching option found', () => {
			const result = formatFieldValue({
				value: 'unknown',
				field: { fieldType: 'select', options: [{ value: 'opt1', label: 'Option 1' }] }
			});
			expect(result).toBe('unknown');
		});

		it('handles combobox the same as select', () => {
			const result = formatFieldValue({
				value: 'opt1',
				field: { fieldType: 'combobox', options: [{ value: 'opt1', label: 'Combo Option 1' }] }
			});
			expect(result).toBe('Combo Option 1');
		});
	});

	describe('percentage formatting', () => {
		it('appends % to percentage values', () => {
			expect(
				formatFieldValue({ value: 50, field: { fieldType: 'percentage', options: null } })
			).toBe('50%');
		});

		it('handles string percentage values', () => {
			expect(
				formatFieldValue({ value: '75', field: { fieldType: 'percentage', options: null } })
			).toBe('75%');
		});
	});

	describe('currency/money formatting', () => {
		it('formats number as GBP by default', () => {
			const result = formatFieldValue({
				value: 1234.56,
				field: { fieldType: 'currency', options: null }
			});
			expect(result).toContain('1,234.56');
		});

		it('uses currency from options', () => {
			const result = formatFieldValue({
				value: 1000,
				field: { fieldType: 'money', options: { currency: 'USD' } }
			});
			expect(result).toMatch(/\$|USD/);
		});

		it('handles string values with commas', () => {
			const result = formatFieldValue({
				value: '1,234,567.89',
				field: { fieldType: 'currency', options: null }
			});
			expect(result).toContain('1,234,567.89');
		});

		it('returns original value for non-numeric string', () => {
			const result = formatFieldValue({
				value: 'not-a-number',
				field: { fieldType: 'currency', options: null }
			});
			expect(result).toBe('not-a-number');
		});
	});

	describe('object values', () => {
		it('JSON stringifies object values', () => {
			const result = formatFieldValue({
				value: { foo: 'bar' },
				field: { fieldType: 'text', options: null }
			});
			expect(result).toBe('{"foo":"bar"}');
		});
	});

	describe('default string conversion', () => {
		it('converts numbers to string', () => {
			expect(formatFieldValue({ value: 123, field: { fieldType: 'text', options: null } })).toBe(
				'123'
			);
		});

		it('converts regular strings', () => {
			expect(
				formatFieldValue({ value: 'hello', field: { fieldType: 'text', options: null } })
			).toBe('hello');
		});
	});

	describe('closed person lists', () => {
		it('labels a marital slug from the shared list, with no options on the field', () => {
			expect(
				formatFieldValue({ value: 'widowed', field: { fieldType: 'marital', options: null } })
			).toBe('Widowed');
		});

		it('labels a stored title the shared list does not name', () => {
			expect(
				formatFieldValue({ value: 'Baroness', field: { fieldType: 'title', options: null } })
			).toBe('Baroness');
		});
	});
});

describe('formatDynamicFieldsForDisplay', () => {
	it('filters out fields with empty formatted values', () => {
		const fields = [
			{
				label: 'Name',
				fieldKey: 'name',
				fieldType: 'text',
				section: null,
				sortOrder: 1,
				conditions: null,
				validation: null,
				options: null
			},
			{
				label: 'Empty',
				fieldKey: 'empty',
				fieldType: 'text',
				section: null,
				sortOrder: 2,
				conditions: null,
				validation: null,
				options: null
			}
		];
		const data = { name: 'John', empty: '' };
		const result = formatDynamicFieldsForDisplay({ fields, data });
		expect(result).toHaveLength(1);
		expect(result[0].formatted).toBe('John');
	});

	it('uses nested paths from fieldKey', () => {
		const fields = [
			{
				label: 'Street',
				fieldKey: 'address.line1',
				fieldType: 'text',
				section: null,
				sortOrder: 1,
				conditions: null,
				validation: null,
				options: null
			}
		];
		const data = { address: { line1: '123 Main St' } };
		const result = formatDynamicFieldsForDisplay({ fields, data });
		expect(result[0].formatted).toBe('123 Main St');
	});
});
