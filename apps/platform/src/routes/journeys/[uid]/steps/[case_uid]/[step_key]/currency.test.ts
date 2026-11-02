import { describe, expect, it } from 'vitest';
import { productCurrencies, withBaseCurrency } from './currency';

const currencies = [
	{ code: 'EUR', is_base: true },
	{ code: 'GBP', is_base: false }
];

describe('withBaseCurrency', () => {
	it('starts a blank currency answer on the firm base', () => {
		expect(withBaseCurrency({}, true, currencies)).toEqual({ currency: 'EUR' });
	});

	it('never replaces a stored answer', () => {
		expect(withBaseCurrency({ currency: 'GBP' }, true, currencies)).toEqual({ currency: 'GBP' });
	});

	it('does not add currency to a form that does not ask for it', () => {
		expect(withBaseCurrency({ name: 'Jane' }, false, currencies)).toEqual({ name: 'Jane' });
	});
});

describe('productCurrencies', () => {
	it('offers only the selected product currencies', () => {
		expect(productCurrencies(currencies, ['GBP'])).toEqual([{ code: 'GBP', is_base: true }]);
	});

	it('keeps the firm base when the product permits it', () => {
		expect(productCurrencies(currencies, ['GBP', 'EUR'])).toEqual([
			{ code: 'EUR', is_base: true },
			{ code: 'GBP', is_base: false }
		]);
	});
});
