import { describe, expect, it } from 'vitest';
import { matchSummary } from './classifications.js';

describe('matchSummary', () => {
	it('says everyone when the rule is empty', () => {
		expect(matchSummary({})).toBe('Everyone');
		expect(matchSummary(null)).toBe('Everyone');
	});

	it('names the advised-vs-D2C predicates in one line', () => {
		expect(
			matchSummary({
				any_account_service_types: ['advised', 'discretionary']
			})
		).toBe('any advised, discretionary account');
		expect(
			matchSummary({
				account_service_types: ['execution_only'],
				max_value: 25_000
			})
		).toBe('only execution-only accounts · up to £25,000');
	});
});
