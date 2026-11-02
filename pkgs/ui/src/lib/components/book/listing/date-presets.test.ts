import { describe, expect, it } from 'vitest';
import { CalendarDate } from '@internationalized/date';
import { DATE_PRESETS, matchingPreset, presetRange } from './date-presets.js';

const TODAY = new CalendarDate(2026, 9, 25);

describe('date presets', () => {
	it('is three bands: a rolling window, the period so far, then the last completed one', () => {
		expect(DATE_PRESETS.map((preset) => preset.label)).toEqual([
			'Last 24 hr',
			'Last 7 days',
			'Last 30 days',
			'Last 3 months',
			'Last 12 months',
			'Month to date',
			'Quarter to date',
			'Year to date',
			'Last month',
			'Last quarter',
			'Last year'
		]);
	});

	it('ends today and starts that far back, both ends inclusive', () => {
		expect(presetRange('24h', TODAY)).toEqual({ since: '2026-09-24', until: '2026-09-25' });
		expect(presetRange('7d', TODAY)).toEqual({ since: '2026-09-18', until: '2026-09-25' });
		expect(presetRange('30d', TODAY)).toEqual({ since: '2026-08-26', until: '2026-09-25' });
		expect(presetRange('3m', TODAY)).toEqual({ since: '2026-06-25', until: '2026-09-25' });
		expect(presetRange('12m', TODAY)).toEqual({ since: '2025-09-25', until: '2026-09-25' });
	});

	it('names the period so far, and the last completed month, quarter and year', () => {
		expect(presetRange('mtd', TODAY)).toEqual({ since: '2026-09-01', until: '2026-09-25' });
		expect(presetRange('qtd', TODAY)).toEqual({ since: '2026-07-01', until: '2026-09-25' });
		expect(presetRange('ytd', TODAY)).toEqual({ since: '2026-01-01', until: '2026-09-25' });
		expect(presetRange('last-month', TODAY)).toEqual({ since: '2026-08-01', until: '2026-08-31' });
		expect(presetRange('last-quarter', TODAY)).toEqual({
			since: '2026-04-01',
			until: '2026-06-30'
		});
		expect(presetRange('last-year', TODAY)).toEqual({ since: '2025-01-01', until: '2025-12-31' });
	});

	it('steps a completed period back across the year, including a leap-day February', () => {
		const january = new CalendarDate(2026, 1, 15);
		expect(presetRange('last-month', january)).toEqual({
			since: '2025-12-01',
			until: '2025-12-31'
		});
		expect(presetRange('last-quarter', january)).toEqual({
			since: '2025-10-01',
			until: '2025-12-31'
		});
		expect(presetRange('qtd', january)).toEqual({ since: '2026-01-01', until: '2026-01-15' });
		// 1 January is month, quarter and year to date at once. The most specific one wins.
		expect(matchingPreset('2026-01-01', '2026-01-15', january)).toBe('mtd');

		const march = new CalendarDate(2024, 3, 1);
		expect(presetRange('last-month', march)).toEqual({ since: '2024-02-01', until: '2024-02-29' });
	});

	it('recognises a range that is a preset, and a hand-picked span as none of them', () => {
		expect(matchingPreset('2026-09-18', '2026-09-25', TODAY)).toBe('7d');
		expect(matchingPreset('2026-01-01', '2026-06-30', TODAY)).toBeNull();
		expect(matchingPreset('', '', TODAY)).toBeNull();
	});

	it('will not start before the listing’s earliest day or end after its latest', () => {
		expect(presetRange('12m', TODAY, { min: '2026-01-01', max: '2026-09-01' })).toEqual({
			since: '2026-01-01',
			until: '2026-09-01'
		});
	});

	it('collapses a completed period that misses the listing window onto the nearest allowed day', () => {
		const january = new CalendarDate(2026, 1, 15);
		const openedThisMonth = { min: '2026-01-10' };
		// December, last quarter and last year all finish before the book opens. Both ends
		// stay on the first day the calendar will offer, not on 31 December.
		expect(presetRange('last-month', january, openedThisMonth)).toEqual({
			since: '2026-01-10',
			until: '2026-01-10'
		});
		expect(presetRange('last-quarter', january, openedThisMonth)).toEqual({
			since: '2026-01-10',
			until: '2026-01-10'
		});
		expect(presetRange('last-year', january, openedThisMonth)).toEqual({
			since: '2026-01-10',
			until: '2026-01-10'
		});
		// A month that still overlaps keeps the overlap.
		expect(presetRange('last-month', TODAY, { min: '2026-08-15' })).toEqual({
			since: '2026-08-15',
			until: '2026-08-31'
		});
	});
});
