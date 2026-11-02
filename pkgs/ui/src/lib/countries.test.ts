import { describe, expect, it } from 'vitest';
import { countryName } from './countries';

describe('countryName', () => {
	it('names a country code, and keeps a code it cannot name', () => {
		expect(countryName('gb')).toBe('United Kingdom');
		expect(countryName('BY')).toBe('Belarus');
		expect(countryName('')).toBe('');
		expect(countryName(null)).toBe('');
		expect(countryName('not a code')).toBe('not a code');
	});
});
