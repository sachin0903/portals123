import { describe, expect, it } from 'vitest';
import { CURRENCY_SYMBOLS } from './currency-symbols.js';
import { FLAGS_BY_COUNTRY, currencyCountry, currencyFlag } from './currency-flags.js';

describe('currencyCountry', () => {
	it('takes the ISO 3166 territory from the front of the ISO 4217 code', () => {
		expect(currencyCountry('GBP')).toBe('gb');
		expect(currencyCountry('USD')).toBe('us');
		expect(currencyCountry('CHF')).toBe('ch');
		expect(currencyCountry('EUR')).toBe('eu');
	});

	it('normalises case and whitespace, as the pill does', () => {
		expect(currencyCountry(' gbp ')).toBe('gb');
	});

	it('gives the X codes (supranational, commodity) no country', () => {
		for (const code of ['XCD', 'XOF', 'XAF', 'XPF', 'XAU', 'XDR', 'XXX']) {
			expect(currencyCountry(code)).toBeUndefined();
		}
	});

	it('refuses anything that is not a three-letter code', () => {
		expect(currencyCountry('')).toBeUndefined();
		expect(currencyCountry(null)).toBeUndefined();
		expect(currencyCountry(undefined)).toBeUndefined();
		expect(currencyCountry('GB')).toBeUndefined();
		expect(currencyCountry('£')).toBeUndefined();
		expect(currencyCountry('GBP1')).toBeUndefined();
	});
});

describe('currencyFlag', () => {
	it('resolves to a circle-flags SVG asset', () => {
		expect(currencyFlag('GBP')).toMatch(/\/gb\.svg(\?|$)/);
		expect(currencyFlag('EUR')).toMatch(/\/eu\.svg(\?|$)/);
	});

	it('is undefined where there is no country', () => {
		expect(currencyFlag('XCD')).toBeUndefined();
		expect(currencyFlag(null)).toBeUndefined();
	});

	it('has a flag for every currency the reference table seeds, X codes aside', () => {
		// The one assumption this module rests on — ISO 4217 prefixes are ISO 3166 codes and the
		// pack carries all of them, including the withdrawn ones the seed still lists (AN, the
		// Netherlands Antilles, for ANG). A code that loses its flag shows up here by name.
		const missing = Object.keys(CURRENCY_SYMBOLS).filter(
			(code) => !code.startsWith('X') && currencyFlag(code) === undefined
		);
		expect(missing).toEqual([]);
	});

	it('only globbed the two-letter territory files', () => {
		for (const key of Object.keys(FLAGS_BY_COUNTRY)) expect(key).toMatch(/^[a-z]{2}$/);
		expect(Object.keys(FLAGS_BY_COUNTRY).length).toBeGreaterThan(200);
	});
});
