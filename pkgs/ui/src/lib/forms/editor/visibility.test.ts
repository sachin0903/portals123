import { describe, expect, it } from 'vitest';
import { getNestedValue, isFieldVisible } from './visibility';

describe('getNestedValue', () => {
	it('returns undefined for empty path', () => {
		expect(getNestedValue({ foo: 'bar' }, '')).toBe(undefined);
	});

	it('returns nested value from simple path', () => {
		expect(getNestedValue({ name: 'John' }, 'name')).toBe('John');
	});

	it('returns nested value from deep path', () => {
		expect(getNestedValue({ address: { line1: '123 Main St' } }, 'address.line1')).toBe(
			'123 Main St'
		);
	});

	it('returns undefined for non-existent path', () => {
		expect(getNestedValue({ foo: 'bar' }, 'baz')).toBe(undefined);
	});

	it('handles application-prefixed paths', () => {
		expect(getNestedValue({ localData: 'x' }, 'application.status', { status: 'draft' })).toBe(
			'draft'
		);
	});

	it('handles applicant-prefixed paths', () => {
		expect(getNestedValue({ name: 'Alice' }, 'applicant.name')).toBe('Alice');
	});

	it('handles account-prefixed paths', () => {
		expect(getNestedValue({ type: 'business' }, 'account.type')).toBe('business');
	});

	it('resolves an account path against a NESTED account object', () => {
		// The shape the API actually sends: `GET /applications/{uid}/steps/…/form` returns
		// `context.account = { product_type, custody, … }`. Stripping the prefix and looking
		// `product_type` up flat found nothing, so every `account.*` condition read as "no" — the
		// seeded onboarding flow's pension-only questions were never asked and its
		// not-a-pension ones always were.
		const context = { account: { product_type: 'SIPP', custody: 'offshore' } };
		expect(getNestedValue({}, 'account.product_type', context)).toBe('SIPP');
		expect(getNestedValue(context, 'account.custody')).toBe('offshore');
	});

	it('prefers a flat answer over the nested context, so an answer still overrides', () => {
		const context = { account: { product_type: 'SIPP' } };
		expect(getNestedValue({ product_type: 'ISA' }, 'account.product_type', context)).toBe('ISA');
	});

	it('reads a dotted FIELD KEY as one key before walking it as a path', () => {
		// The form keeps answers flat by `fieldKey`. The seeded employer step's company fields are
		// conditional on `pension.hasEmployerContributions equals true`; walking that as
		// `values.pension.hasEmployerContributions` found nothing, so ticking the box revealed
		// nothing and no employer was ever captured.
		const values = { 'pension.hasEmployerContributions': true };
		expect(getNestedValue(values, 'pension.hasEmployerContributions')).toBe(true);
		expect(
			isFieldVisible(
				{
					conditions: {
						conditional: {
							field: 'pension.hasEmployerContributions',
							operator: 'equals' as const,
							value: true
						}
					},
					validation: null
				},
				values
			)
		).toBe(true);
		// The nested walk is still the fallback for a composite answer.
		expect(getNestedValue({ address: { line1: '1 High St' } }, 'address.line1')).toBe('1 High St');
	});
});

describe('isFieldVisible - evaluateCondition edge cases', () => {
	describe('contains operator with null/undefined values', () => {
		it('should return false for contains when field value is null', () => {
			const field = {
				conditions: { field: 'status', operator: 'contains' as const, value: 'active' },
				validation: null
			};
			expect(isFieldVisible(field, { status: null })).toBe(false);
		});

		it('should return false for contains when field value is undefined', () => {
			const field = {
				conditions: { field: 'status', operator: 'contains' as const, value: 'def' },
				validation: null
			};
			expect(isFieldVisible(field, {})).toBe(false);
		});

		it('should return true for notContains when field value is null', () => {
			const field = {
				conditions: { field: 'status', operator: 'notContains' as const, value: 'active' },
				validation: null
			};
			expect(isFieldVisible(field, { status: null })).toBe(true);
		});

		it('should return true for notContains when field value is undefined', () => {
			const field = {
				conditions: { field: 'status', operator: 'notContains' as const, value: 'test' },
				validation: null
			};
			expect(isFieldVisible(field, {})).toBe(true);
		});
	});

	describe('isEmpty/isNotEmpty with arrays', () => {
		it('isEmpty should return true for empty array', () => {
			const field = {
				conditions: { field: 'items', operator: 'isEmpty' as const },
				validation: null
			};
			expect(isFieldVisible(field, { items: [] })).toBe(true);
		});

		it('isNotEmpty should return false for empty array', () => {
			const field = {
				conditions: { field: 'items', operator: 'isNotEmpty' as const },
				validation: null
			};
			expect(isFieldVisible(field, { items: [] })).toBe(false);
		});

		it('isEmpty should return false for non-empty array', () => {
			const field = {
				conditions: { field: 'items', operator: 'isEmpty' as const },
				validation: null
			};
			expect(isFieldVisible(field, { items: ['a', 'b'] })).toBe(false);
		});

		it('isNotEmpty should return true for non-empty array', () => {
			const field = {
				conditions: { field: 'items', operator: 'isNotEmpty' as const },
				validation: null
			};
			expect(isFieldVisible(field, { items: ['a', 'b'] })).toBe(true);
		});
	});

	describe('in/notIn with non-array cond.value', () => {
		it('in operator should return false when cond.value is not an array', () => {
			const field = {
				conditions: { field: 'status', operator: 'in' as const, value: 'active' },
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active' })).toBe(false);
		});

		it('notIn operator should return true when cond.value is not an array (cannot be in nothing)', () => {
			const field = {
				conditions: { field: 'status', operator: 'notIn' as const, value: 'active' },
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active' })).toBe(true);
		});
	});

	describe('basic operators work correctly', () => {
		it('equals operator works', () => {
			const field = {
				conditions: { field: 'status', operator: 'equals' as const, value: 'active' },
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active' })).toBe(true);
			expect(isFieldVisible(field, { status: 'inactive' })).toBe(false);
		});

		it('notEquals operator works', () => {
			const field = {
				conditions: { field: 'status', operator: 'notEquals' as const, value: 'active' },
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active' })).toBe(false);
			expect(isFieldVisible(field, { status: 'inactive' })).toBe(true);
		});

		it('in operator works with array value', () => {
			const field = {
				conditions: { field: 'status', operator: 'in' as const, value: ['active', 'pending'] },
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active' })).toBe(true);
			expect(isFieldVisible(field, { status: 'inactive' })).toBe(false);
		});

		it('notIn operator works with array value', () => {
			const field = {
				conditions: { field: 'status', operator: 'notIn' as const, value: ['active', 'pending'] },
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active' })).toBe(false);
			expect(isFieldVisible(field, { status: 'inactive' })).toBe(true);
		});
	});

	describe('missing field or operator', () => {
		it('returns true when condition has no field', () => {
			const field = { conditions: { operator: 'equals', value: 'test' }, validation: null };
			expect(isFieldVisible(field as never, {})).toBe(true);
		});

		it('returns true when condition has no operator', () => {
			const field = { conditions: { field: 'status', value: 'test' }, validation: null };
			expect(isFieldVisible(field as never, { status: 'test' })).toBe(true);
		});
	});

	describe('AND/OR logic', () => {
		it('AND logic requires all conditions to be true', () => {
			const field = {
				conditions: {
					conditional: {
						logic: 'AND' as const,
						conditions: [
							{ field: 'status', operator: 'equals' as const, value: 'active' },
							{ field: 'type', operator: 'equals' as const, value: 'business' }
						]
					}
				},
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active', type: 'business' })).toBe(true);
			expect(isFieldVisible(field, { status: 'active', type: 'personal' })).toBe(false);
		});

		it('OR logic requires at least one condition to be true', () => {
			const field = {
				conditions: {
					conditional: {
						logic: 'OR' as const,
						conditions: [
							{ field: 'status', operator: 'equals' as const, value: 'active' },
							{ field: 'type', operator: 'equals' as const, value: 'business' }
						]
					}
				},
				validation: null
			};
			expect(isFieldVisible(field, { status: 'active', type: 'personal' })).toBe(true);
			expect(isFieldVisible(field, { status: 'inactive', type: 'business' })).toBe(true);
			expect(isFieldVisible(field, { status: 'inactive', type: 'personal' })).toBe(false);
		});

		it('evaluates a group nested inside another group', () => {
			const field = {
				conditions: {
					conditional: {
						logic: 'AND' as const,
						conditions: [
							{
								field: 'national_identifier_type',
								operator: 'notEquals' as const,
								value: 'National Insurance Number'
							},
							{
								logic: 'OR' as const,
								conditions: [
									{
										field: 'applicant.has_isa_or_pension',
										operator: 'equals' as const,
										value: true
									},
									{ field: 'nationality', operator: 'contains' as const, value: 'GB' }
								]
							}
						]
					}
				},
				validation: null
			};
			// Nested groups are what the runtime reads. The editor's stored type is still a flat
			// list, so this fixture is the shape on the wire rather than a FormField.
			const visible = field as Parameters<typeof isFieldVisible>[0];
			const context = { applicant: { has_isa_or_pension: false } };
			expect(
				isFieldVisible(
					visible,
					{ national_identifier_type: 'Passport', nationality: ['NZ'] },
					{ applicant: { has_isa_or_pension: true } }
				)
			).toBe(true);
			expect(
				isFieldVisible(
					visible,
					{ national_identifier_type: 'National Insurance Number', nationality: ['GB'] },
					{ applicant: { has_isa_or_pension: true } }
				)
			).toBe(false);
			expect(
				isFieldVisible(
					visible,
					{ national_identifier_type: 'Passport', nationality: ['NZ'] },
					context
				)
			).toBe(false);
		});
	});
});
