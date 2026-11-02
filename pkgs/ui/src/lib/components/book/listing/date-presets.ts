/** The date filter's preset list, and the calendar dates each one means.
 *
 *  Three bands, drawn with a separator between them: a rolling window back from today, the
 *  current month/quarter/year so far, and the last completed month/quarter/year. The ledger
 *  stores a range as `YYYY-MM-DD`, so "last 24 hr" is the calendar day that covers it —
 *  yesterday through today — not a timestamp. A rolling window and a to-date window end today
 *  (or `max`, when the listing will not offer a later day). A completed period does not: last
 *  month is the whole previous month. Both ends are inclusive.
 */
import { type CalendarDate } from '@internationalized/date';
import { toCalendarDate, toDateText } from './date-range.js';

export const DATE_PRESET_GROUPS = [
	[
		{ id: '24h', label: 'Last 24 hr' },
		{ id: '7d', label: 'Last 7 days' },
		{ id: '30d', label: 'Last 30 days' },
		{ id: '3m', label: 'Last 3 months' },
		{ id: '12m', label: 'Last 12 months' }
	],
	[
		{ id: 'mtd', label: 'Month to date' },
		{ id: 'qtd', label: 'Quarter to date' },
		{ id: 'ytd', label: 'Year to date' }
	],
	[
		{ id: 'last-month', label: 'Last month' },
		{ id: 'last-quarter', label: 'Last quarter' },
		{ id: 'last-year', label: 'Last year' }
	]
] as const;

export const DATE_PRESETS = DATE_PRESET_GROUPS.flat();

export type DatePresetId = (typeof DATE_PRESETS)[number]['id'];

const SPAN: Partial<Record<DatePresetId, { days?: number; months?: number }>> = {
	'24h': { days: 1 },
	'7d': { days: 7 },
	'30d': { days: 30 },
	'3m': { months: 3 },
	'12m': { months: 12 }
};

export function isDatePreset(id: string): id is DatePresetId {
	return DATE_PRESETS.some((preset) => preset.id === id);
}

/** The first day of the quarter `date` sits in. */
function quarterStart(date: CalendarDate): CalendarDate {
	const month = Math.floor((date.month - 1) / 3) * 3 + 1;
	return date.set({ month, day: 1 });
}

/** The unclamped span. Rolling and to-date windows end on `end`; a completed period replaces it. */
function windowFor(
	id: DatePresetId,
	end: CalendarDate
): { start: CalendarDate; end: CalendarDate } {
	switch (id) {
		case '24h':
		case '7d':
		case '30d':
		case '3m':
		case '12m': {
			const span = SPAN[id];
			const start = span?.days
				? end.subtract({ days: span.days })
				: end.subtract({ months: span?.months ?? 0 });
			return { start, end };
		}
		case 'mtd':
			return { start: end.set({ day: 1 }), end };
		case 'qtd':
			return { start: quarterStart(end), end };
		case 'ytd':
			return { start: end.set({ month: 1, day: 1 }), end };
		case 'last-month': {
			const last = end.set({ day: 1 }).subtract({ days: 1 });
			return { start: last.set({ day: 1 }), end: last };
		}
		case 'last-quarter': {
			const last = quarterStart(end).subtract({ days: 1 });
			return { start: quarterStart(last), end: last };
		}
		case 'last-year': {
			const year = end.year - 1;
			return {
				start: end.set({ year, month: 1, day: 1 }),
				end: end.set({ year, month: 12, day: 31 })
			};
		}
	}
}

/** Both ends inclusive. Each end is pulled into the listing's window, so a preset cannot
 *  offer a day the calendar itself refuses. A completed period that misses that window
 *  entirely — last month on a book that opened this month — collapses onto the nearest
 *  day the calendar will offer. Pulling the start back to the old end would land it
 *  outside the window again. */
export function presetRange(
	id: DatePresetId,
	now: CalendarDate,
	bounds?: { min?: string; max?: string }
): { since: string; until: string } {
	const max = toCalendarDate(bounds?.max);
	const min = toCalendarDate(bounds?.min);
	let anchor = now;
	if (max && anchor.compare(max) > 0) anchor = max;
	let { start, end } = windowFor(id, anchor);
	const intoWindow = (date: CalendarDate): CalendarDate => {
		if (min && date.compare(min) < 0) date = min;
		if (max && date.compare(max) > 0) date = max;
		return date;
	};
	start = intoWindow(start);
	end = intoWindow(end);
	// The listing's own bounds are the only way this still inverts, and `end` is already
	// inside them. A missed period has been pinned to one day, so it must not be reopened.
	if (start.compare(end) > 0) start = end;
	return { since: toDateText(start), until: toDateText(end) };
}

/** Which preset the stored range IS, if it is one. A hand-picked span matches none of them. */
export function matchingPreset(
	since: string,
	until: string,
	now: CalendarDate,
	bounds?: { min?: string; max?: string }
): DatePresetId | null {
	for (const preset of DATE_PRESETS) {
		const range = presetRange(preset.id, now, bounds);
		if (range.since === since && range.until === until) return preset.id;
	}
	return null;
}
