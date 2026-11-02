import { describe, expect, it } from 'vitest';
import { CalendarDate } from '@internationalized/date';
import { toCalendarDate, toDateText } from './date-range.js';

describe('toCalendarDate', () => {
	it('reads the wire format the listings store', () => {
		expect(toCalendarDate('2026-09-17')).toEqual(new CalendarDate(2026, 9, 17));
	});

	it('treats anything it cannot read as no bound, so a hand-edited URL narrows nothing', () => {
		for (const bad of ['', '2026-09', 'yesterday', '2026-13-01', '2026-00-10', '2026-09-32']) {
			expect(toCalendarDate(bad), bad).toBeUndefined();
		}
		expect(toCalendarDate(undefined)).toBeUndefined();
		expect(toCalendarDate(null)).toBeUndefined();
	});

	it('refuses a day that does not exist rather than rolling it into the next month', () => {
		// CalendarDate would balance this to 3 March, handing back a date nobody typed.
		expect(toCalendarDate('2026-02-31')).toBeUndefined();
		// …but a real leap day still reads.
		expect(toCalendarDate('2028-02-29')).toEqual(new CalendarDate(2028, 2, 29));
	});
});

describe('toDateText', () => {
	it('zero-pads, so the strings compare as dates do', () => {
		expect(toDateText(new CalendarDate(2026, 1, 5))).toBe('2026-01-05');
		expect(toDateText(new CalendarDate(2026, 12, 31))).toBe('2026-12-31');
	});

	it('is empty for no bound — an open-ended range, not the epoch', () => {
		expect(toDateText(undefined)).toBe('');
		expect(toDateText(null)).toBe('');
	});

	it('round-trips whatever toCalendarDate accepted', () => {
		for (const text of ['2026-09-17', '2028-02-29', '2026-01-05']) {
			expect(toDateText(toCalendarDate(text)), text).toBe(text);
		}
	});
});
