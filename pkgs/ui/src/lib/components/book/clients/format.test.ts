import { describe, expect, it } from 'vitest';
import { clientName, clientSubline, providerReference, shortLocation } from './format.js';

describe('providerReference', () => {
	it('strips the seccl_ provider prefix', () => {
		expect(providerReference('seccl_CXF421C15')).toBe('CXF421C15');
	});

	it('strips the sei_ provider prefix', () => {
		expect(providerReference('sei_ABC123')).toBe('ABC123');
	});

	it('strips the ml_ provider prefix', () => {
		expect(providerReference('ml_ABC123')).toBe('ABC123');
	});

	it('returns an unprefixed uid unchanged', () => {
		expect(providerReference('CXF421C15')).toBe('CXF421C15');
	});

	it('only strips a leading prefix, not one that merely appears inside the value', () => {
		expect(providerReference('CXseccl_15')).toBe('CXseccl_15');
	});
});

describe('clientName', () => {
	// A nameless client/organisation falls back to its uid, which is provider-prefixed in
	// storage (see PROVIDER_UID_PREFIXES above) — the exact leak providerReference exists to
	// strip. Regression case: both fallback branches must go through it too.
	it('strips the provider prefix from a nameless individual’s uid fallback', () => {
		expect(clientName({ type: 'individual', uid: 'seccl_CXF421C15' })).toBe('CXF421C15');
	});

	it('strips the provider prefix from a nameless organisation’s uid fallback', () => {
		expect(clientName({ type: 'organisation', uid: 'seccl_CXF421C15' })).toBe('CXF421C15');
	});

	it('still prefers a real name over the uid fallback', () => {
		expect(
			clientName({
				type: 'individual',
				first_name: 'Max',
				surname: 'Oakden',
				uid: 'seccl_CXF421C15'
			})
		).toBe('Max Oakden');
	});
});

describe('shortLocation', () => {
	it('is town, postcode and country — the header’s address line without the street', () => {
		expect(
			shortLocation({ line1: '1 Meneage St', city: 'Helston', postcode: 'TR13 9PU', country: 'GB' })
		).toBe('Helston, TR13 9PU, GB');
	});

	it('drops blank parts rather than printing empty commas', () => {
		expect(shortLocation({ city: '', postcode: 'TR13 9PU', country: null })).toBe('TR13 9PU');
		expect(shortLocation({ city: ' London ', country: 'GB' })).toBe('London, GB');
	});

	it('is null when nothing locating is stored, whatever shape arrived', () => {
		expect(shortLocation(null)).toBeNull();
		expect(shortLocation(undefined)).toBeNull();
		expect(shortLocation({})).toBeNull();
		expect(shortLocation({ line1: '1 Meneage St' })).toBeNull();
		expect(shortLocation('Helston')).toBeNull();
	});
});

describe('clientSubline', () => {
	const address = { city: 'Helston', postcode: 'TR13 9PU', country: 'GB' };

	it('prefers the date of birth, printed as the record prints it', () => {
		expect(clientSubline({ dob: '1962-08-05', address })).toBe('5 Aug 1962');
	});

	it('falls back to where they are when there is no date — blank or unparseable counts as none', () => {
		expect(clientSubline({ dob: null, address })).toBe('Helston, TR13 9PU, GB');
		expect(clientSubline({ dob: '  ', address })).toBe('Helston, TR13 9PU, GB');
		expect(clientSubline({ dob: 'unknown', address })).toBe('Helston, TR13 9PU, GB');
	});

	it('is null with neither, so the row shows the name alone', () => {
		expect(clientSubline({ dob: null, address: null })).toBeNull();
		expect(clientSubline({})).toBeNull();
	});
});
