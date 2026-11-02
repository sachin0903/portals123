import { describe, expect, it } from 'vitest';
import type { FieldOptions } from '../types.js';
import { fieldChoices, resolveOptions } from './options';

describe('resolveOptions', () => {
	describe('empty/null options handling', () => {
		it('returns empty array for null options', () => {
			expect(resolveOptions({ options: null })).toEqual([]);
		});

		it('returns empty array for undefined options', () => {
			expect(resolveOptions({ options: undefined as unknown as FieldOptions })).toEqual([]);
		});

		it('returns empty array for empty array', () => {
			expect(resolveOptions({ options: [] })).toEqual([]);
		});
	});

	describe('string array options', () => {
		it('converts string array to value/label pairs', () => {
			const result = resolveOptions({ options: ['foo', 'bar'] });
			expect(result).toEqual([
				{ value: 'foo', label: 'foo' },
				{ value: 'bar', label: 'bar' }
			]);
		});
	});

	describe('object array options', () => {
		it('handles value/label objects', () => {
			const result = resolveOptions({
				options: [
					{ value: 'a', label: 'Option A' },
					{ value: 'b', label: 'Option B' }
				]
			});
			expect(result).toEqual([
				{ value: 'a', label: 'Option A' },
				{ value: 'b', label: 'Option B' }
			]);
		});

		it('uses value as label when label is missing', () => {
			const result = resolveOptions({ options: [{ value: 'a' }, { value: 'b' }] });
			expect(result).toEqual([
				{ value: 'a', label: 'a' },
				{ value: 'b', label: 'b' }
			]);
		});

		it('filters out options with null/undefined value', () => {
			const result = resolveOptions({
				options: [
					{ value: 'valid' },
					{ value: null },
					{ value: undefined },
					{ value: 'also-valid' }
				]
			});
			expect(result).toEqual([
				{ value: 'valid', label: 'valid' },
				{ value: 'also-valid', label: 'also-valid' }
			]);
		});

		it('handles code/name/adjective objects', () => {
			const result = resolveOptions({
				options: [
					{ code: 'GB', name: 'United Kingdom', adjective: 'British' },
					{ code: 'US', name: 'United States', adjective: 'American' }
				]
			});
			expect(result).toEqual([
				{ value: 'GB', label: 'United Kingdom' },
				{ value: 'US', label: 'United States' }
			]);
		});

		it('filters out options with null/undefined code', () => {
			const result = resolveOptions({
				options: [
					{ code: 'GB', name: 'United Kingdom' },
					{ code: null, name: 'Invalid' },
					{ code: undefined, name: 'Also Invalid' }
				]
			});
			expect(result).toEqual([{ value: 'GB', label: 'United Kingdom' }]);
		});
	});

	describe('nested options object', () => {
		it('handles { options: [...] } wrapper', () => {
			const result = resolveOptions({ options: { options: ['foo', 'bar'] } });
			expect(result).toEqual([
				{ value: 'foo', label: 'foo' },
				{ value: 'bar', label: 'bar' }
			]);
		});
	});

	describe('data source references', () => {
		it('resolves options from data source', () => {
			const dataSources = {
				countries: [
					{ id: 'GB', name: 'United Kingdom' },
					{ id: 'US', name: 'United States' }
				]
			};
			const result = resolveOptions({ options: { source: 'countries' } }, dataSources);
			expect(result).toEqual([
				{ value: 'GB', label: 'United Kingdom' },
				{ value: 'US', label: 'United States' }
			]);
		});

		it('uses custom valueKey and labelKey', () => {
			const dataSources = {
				currencies: [
					{ code: 'GBP', display: 'British Pound' },
					{ code: 'USD', display: 'US Dollar' }
				]
			};
			const result = resolveOptions(
				{ options: { source: 'currencies', valueKey: 'code', labelKey: 'display' } },
				dataSources
			);
			expect(result).toEqual([
				{ value: 'GBP', label: 'British Pound' },
				{ value: 'USD', label: 'US Dollar' }
			]);
		});

		it('returns empty array for missing data source', () => {
			const result = resolveOptions({ options: { source: 'nonexistent' } }, {});
			expect(result).toEqual([]);
		});

		it('returns empty array for empty source string', () => {
			const result = resolveOptions({ options: { source: '' } }, { '': [] });
			expect(result).toEqual([]);
		});

		it('returns empty array for whitespace-only source', () => {
			const result = resolveOptions({ options: { source: '   ' } }, {});
			expect(result).toEqual([]);
		});

		describe('data source item with undefined valueKey', () => {
			it('should filter out items where valueKey property is undefined', () => {
				const dataSources = {
					items: [
						{ id: 'valid', name: 'Valid Item' },
						{ name: 'Missing ID' },
						{ id: '', name: 'Empty ID' }
					]
				};
				const result = resolveOptions({ options: { source: 'items' } }, dataSources);
				expect(result).toEqual([{ value: 'valid', label: 'Valid Item' }]);
			});

			it('should filter out items where valueKey is null', () => {
				const dataSources = {
					items: [
						{ id: 'valid', name: 'Valid Item' },
						{ id: null, name: 'Null ID' }
					]
				};
				const result = resolveOptions({ options: { source: 'items' } }, dataSources);
				expect(result).toEqual([{ value: 'valid', label: 'Valid Item' }]);
			});
		});
	});

	describe('nationalities special case', () => {
		it('disambiguates duplicate adjectives with country name', () => {
			const dataSources = {
				nationalities: [
					{ code: 'GB', name: 'United Kingdom', adjective: 'British' },
					{ code: 'VI', name: 'British Virgin Islands', adjective: 'British' },
					{ code: 'US', name: 'United States', adjective: 'American' }
				]
			};
			const result = resolveOptions({ options: { source: 'nationalities' } }, dataSources);
			expect(result).toContainEqual({ value: 'GB', label: 'British (United Kingdom)' });
			expect(result).toContainEqual({ value: 'VI', label: 'British (British Virgin Islands)' });
			expect(result).toContainEqual({ value: 'US', label: 'American' });
		});

		it('handles nationality with null name - should not disambiguate', () => {
			const dataSources = {
				nationalities: [
					{ code: 'GB', name: 'United Kingdom', adjective: 'British' },
					{ code: 'XX', name: null, adjective: 'British' }
				]
			};
			const result = resolveOptions({ options: { source: 'nationalities' } }, dataSources);
			const xxItem = result.find((r) => r.value === 'XX');
			expect(xxItem?.label).not.toContain('null');
		});
	});
});

describe('fieldChoices', () => {
	it('uses the shared marital list and ignores options authored on the field', () => {
		const result = fieldChoices({
			fieldType: 'marital',
			options: [{ value: 'civil_partner', label: 'Civil partner' }]
		});
		expect(result.map((option) => option.value)).toEqual([
			'single',
			'married',
			'divorced',
			'widowed'
		]);
	});

	it('keeps a stored title the shared list does not name, and drops a bare code', () => {
		expect(fieldChoices({ fieldType: 'title', options: null }, {}, 'Baroness').at(-1)).toEqual({
			value: 'Baroness',
			label: 'Baroness'
		});
		expect(
			fieldChoices({ fieldType: 'title', options: null }, {}, '12').map((o) => o.value)
		).toEqual(['Mr', 'Mrs', 'Miss', 'Ms', 'Dr', 'Prof']);
	});
});
