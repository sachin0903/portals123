import { describe, expect, it } from 'vitest';
import { asContributionSummary } from './contribution-summary';

describe('asContributionSummary', () => {
	it('maps the snake_case summary the API returns', () => {
		expect(
			asContributionSummary({
				tax_year: '2026/27',
				account_gross: 1000,
				count: 2
			})
		).toEqual({
			taxYear: '2026/27',
			accountGross: 1000,
			count: 2
		});
	});

	it('returns null when the payload has no tax year', () => {
		expect(asContributionSummary({})).toBeNull();
		expect(asContributionSummary(null)).toBeNull();
	});
});
