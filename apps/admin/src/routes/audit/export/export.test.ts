import { describe, expect, it } from 'vitest';
import { _nextDay } from './+server';

describe('_nextDay', () => {
	it('turns an inclusive end date into the exclusive bound the API expects', () => {
		// The API applies `created_at < :until`, so the whole of the 28th means "before the 29th".
		// Stamping 23:59:59.999 dropped the final millisecond of the day.
		expect(_nextDay('2026-07-28')).toBe('2026-07-29');
	});

	it('rolls over a month and a year boundary', () => {
		expect(_nextDay('2026-07-31')).toBe('2026-08-01');
		expect(_nextDay('2026-12-31')).toBe('2027-01-01');
	});

	it('handles a leap day', () => {
		expect(_nextDay('2028-02-28')).toBe('2028-02-29');
		expect(_nextDay('2028-02-29')).toBe('2028-03-01');
	});

	it('passes anything that is not a plain date straight through (the API validates it)', () => {
		expect(_nextDay('yesterday')).toBe('yesterday');
		expect(_nextDay('')).toBe('');
	});
});
