import { describe, expect, it } from 'vitest';
import { clientTypeLabel, ORGANISATION_SUB_TYPES } from './client-type.js';

describe('clientTypeLabel', () => {
	it('labels both levels of the vocabulary', () => {
		expect(clientTypeLabel('individual')).toBe('Individual');
		expect(clientTypeLabel('organisation')).toBe('Organisation');
		expect(clientTypeLabel('company')).toBe('Company');
		expect(clientTypeLabel('trust')).toBe('Trust');
		expect(clientTypeLabel('charity')).toBe('Charity');
	});

	it('is tolerant of case and space, as a value read from a text cast needs', () => {
		expect(clientTypeLabel(' Trust ')).toBe('Trust');
	});

	// Null, so a caller can fall back to the raw value rather than print "Undefined" — a client
	// whose kind we do not recognise is a data question, not a blank.
	it('says nothing for an absent or unknown kind', () => {
		expect(clientTypeLabel(null)).toBeNull();
		expect(clientTypeLabel(undefined)).toBeNull();
		expect(clientTypeLabel('')).toBeNull();
		expect(clientTypeLabel('partnership')).toBeNull();
	});
});

describe('ORGANISATION_SUB_TYPES', () => {
	// A FIXED list, not a facet: a firm's first trust has to be recordable, and a facet can only
	// offer what the book already holds.
	it('offers every sub-type crm.contact_sub_type allows, labelled by the same function', () => {
		expect(ORGANISATION_SUB_TYPES.map((option) => option.value)).toEqual([
			'company',
			'trust',
			'charity'
		]);
		for (const option of ORGANISATION_SUB_TYPES) {
			expect(clientTypeLabel(option.value)).toBe(option.label);
		}
	});
});
