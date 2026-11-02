import { describe, expect, it } from 'vitest';
import {
	allowanceFact,
	consolidatedCash,
	pensionSplit,
	ukTaxYear,
	wrapperAllowance
} from './money-facts.js';

describe('wrapperAllowance', () => {
	it('is null when the custodian has stated nothing', () => {
		expect(wrapperAllowance({})).toBeNull();
		expect(wrapperAllowance({ allowance_used: 5000 })).toBeNull();
	});

	it('is null for a zero limit — "not stated", not "fully subscribed"', () => {
		expect(wrapperAllowance({ allowance_used: 0, allowance_limit: 0 })).toBeNull();
	});

	it('shows £0 left against a real limit', () => {
		expect(wrapperAllowance({ allowance_used: 20_000, allowance_limit: 20_000 })).toEqual({
			available: 0,
			used: 20_000,
			limit: 20_000,
			taxYear: null
		});
	});

	it('prefers the stated remaining and adds the tax year', () => {
		expect(
			wrapperAllowance({
				allowance_used: 5000,
				allowance_limit: 26_000,
				allowance_remaining: 21_000,
				allowance_tax_year: '2026/27'
			})
		).toEqual({
			available: 21_000,
			sub: '2026/27',
			used: 5000,
			limit: 26_000,
			taxYear: '2026/27'
		});
	});

	it('shows a remaining-only figure without inventing a "used of" line', () => {
		expect(wrapperAllowance({ allowance_remaining: 12_000 })).toEqual({
			available: 12_000,
			used: null,
			limit: null,
			taxYear: null
		});
	});
});

describe('ukTaxYear', () => {
	it('turns over on 6 April', () => {
		expect(ukTaxYear(new Date(2026, 3, 5))).toBe('2025/26');
		expect(ukTaxYear(new Date(2026, 3, 6))).toBe('2026/27');
		expect(ukTaxYear(new Date(2026, 9, 1))).toBe('2026/27');
		expect(ukTaxYear(new Date(2027, 0, 15))).toBe('2026/27');
	});
});

describe('allowanceFact', () => {
	const now = new Date(2026, 9, 1);

	it('is the custodian’s figure when stated', () => {
		expect(
			allowanceFact(
				{
					product_type: 'ISA',
					allowance_used: 5000,
					allowance_limit: 20_000,
					allowance_tax_year: '2026/27'
				},
				now
			)
		).toEqual({
			label: 'ISA allowance used',
			value: 5000,
			sub: '£15,000.00 left · 2026/27',
			stated: true
		});
	});

	it('falls back to the statutory annual limit for an ISA/JISA the custodian is silent on', () => {
		expect(allowanceFact({ product_type: 'ISA' }, now)).toEqual({
			label: 'ISA allowance',
			value: 20_000,
			sub: 'Annual limit for 2026/27 · amount used not yet reported',
			stated: false
		});
		expect(allowanceFact({ product_type: 'jisa' }, now)?.value).toBe(9000);
		expect(allowanceFact({ product_type: 'JISA' }, now)?.label).toBe('JISA allowance');
	});

	it('is null for a wrapper with no subscription allowance', () => {
		expect(allowanceFact({ product_type: 'GIA' }, now)).toBeNull();
		expect(allowanceFact({ product_type: 'SIPP' }, now)).toBeNull();
		expect(allowanceFact({ product_type: null }, now)).toBeNull();
	});

	it('does not read a zero limit as a stated one', () => {
		// Zero is "not stated"; the statutory limit stands in rather than "£0 left".
		expect(
			allowanceFact({ product_type: 'ISA', allowance_used: 0, allowance_limit: 0 }, now)
		).toMatchObject({ stated: false, value: 20_000 });
	});
});

describe('consolidatedCash', () => {
	it('adds administrator cash and lists where it sits', () => {
		expect(
			consolidatedCash(1000, [
				{ source: 'seccl', cash: null, as_of: null, crystallised: 1, uncrystallised: 2 },
				{ source: 'morgan-lloyd', cash: 250, as_of: null, crystallised: null, uncrystallised: null }
			])
		).toEqual({ total: 1250, breakdown: '£1,000.00 custodian · £250.00 Morgan Lloyd' });
	});

	it('has no breakdown with one place', () => {
		expect(consolidatedCash(1000, [])).toEqual({ total: 1000 });
	});
});

describe('pensionSplit', () => {
	it('is null without a split and lets the administrator win', () => {
		expect(pensionSplit([])).toBeNull();
		expect(
			pensionSplit([
				{ source: 'seccl', cash: null, as_of: null, crystallised: 10, uncrystallised: 90 },
				{ source: 'morgan-lloyd', cash: 5, as_of: null, crystallised: 20, uncrystallised: 80 }
			])
		).toEqual({ crystallised: 20, uncrystallised: 80 });
	});
});
