import { describe, expect, it } from 'vitest';
import { grossOf, reliefOf, taxYearOptions } from './relief';

describe('grossOf', () => {
	it('grosses a personal contribution up by basic rate', () => {
		// £800 net is £1,000 gross — the scheme reclaims the £200.
		expect(grossOf({ amount: 800, category: 'personal_contribution' })).toBe(1000);
	});

	it('treats a third-party contribution as paid net, like the member had paid it', () => {
		expect(grossOf({ amount: 800, category: 'third_party_contribution' })).toBe(1000);
	});

	it('leaves an employer contribution alone — it is already gross', () => {
		expect(grossOf({ amount: 1000, category: 'employer_contribution' })).toBe(1000);
	});

	it('leaves a transfer in alone', () => {
		expect(grossOf({ amount: 60000, category: 'transfer_in' })).toBe(60000);
	});
});

describe('reliefOf', () => {
	it('is the difference between gross and net for a member contribution', () => {
		expect(reliefOf({ amount: 800, category: 'personal_contribution' })).toBe(200);
	});

	it('is zero for anything paid gross', () => {
		expect(reliefOf({ amount: 1000, category: 'employer_contribution' })).toBe(0);
		expect(reliefOf({ amount: 1000, category: 'transfer_in' })).toBe(0);
		expect(reliefOf({ amount: 1000, category: 'not_a_contribution' })).toBe(0);
	});
});

describe('taxYearOptions', () => {
	it('starts the year on 6 April', () => {
		expect(taxYearOptions(new Date('2026-04-06'))[0]).toBe('2026/27');
		expect(taxYearOptions(new Date('2026-04-05'))[0]).toBe('2025/26');
	});

	it('treats a date later in the year as the current tax year', () => {
		expect(taxYearOptions(new Date('2026-12-31'))[0]).toBe('2026/27');
	});

	it('treats January as still the previous tax year', () => {
		expect(taxYearOptions(new Date('2027-01-15'))[0]).toBe('2026/27');
	});

	it('returns the requested number of years, most recent first', () => {
		expect(taxYearOptions(new Date('2026-07-28'), 3)).toEqual(['2026/27', '2025/26', '2024/25']);
	});

	it('pads the second year across a century boundary', () => {
		expect(taxYearOptions(new Date('2099-07-01'), 1)).toEqual(['2099/00']);
	});
});
