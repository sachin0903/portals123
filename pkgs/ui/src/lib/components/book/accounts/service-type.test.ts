import { describe, expect, it } from 'vitest';
import { serviceTypeLabel, SERVICE_TYPE_OPTIONS } from './service-type.js';

describe('serviceTypeLabel', () => {
	it('labels the three services the account column shows', () => {
		expect(serviceTypeLabel('discretionary')).toBe('Discretionary');
		expect(serviceTypeLabel('advisory')).toBe('Advisory');
		expect(serviceTypeLabel('execution_only')).toBe('Execution only');
	});

	// The account column stores `advisory`; the same question asked of the PERSON
	// (crm.contacts.service_type) stores `advised`. One service, one label.
	it('reads the contact enum spelling of advisory as the same service', () => {
		expect(serviceTypeLabel('advised')).toBe('Advisory');
	});

	it('is tolerant of case and surrounding space, as a stored text column needs', () => {
		expect(serviceTypeLabel(' Execution_Only ')).toBe('Execution only');
	});

	// The whole point of the null: an account fed by a custodian with no model carries neither
	// fact, and advisory vs execution-only cannot be told apart from the rows we hold.
	it('says nothing rather than guessing when the value is absent or unknown', () => {
		expect(serviceTypeLabel(null)).toBeNull();
		expect(serviceTypeLabel(undefined)).toBeNull();
		expect(serviceTypeLabel('')).toBeNull();
		expect(serviceTypeLabel('managed_somehow')).toBeNull();
	});
});

describe('SERVICE_TYPE_OPTIONS', () => {
	it('offers the whole vocabulary, every value labelled by the same function', () => {
		expect(SERVICE_TYPE_OPTIONS.map((option) => option.value)).toEqual([
			'discretionary',
			'advisory',
			'execution_only'
		]);
		for (const option of SERVICE_TYPE_OPTIONS) {
			expect(serviceTypeLabel(option.value)).toBe(option.label);
		}
	});
});
