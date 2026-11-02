// British short dates (`5 Aug 2026`). A DATE-ONLY value is not an instant: `Date.parse` reads it as
// UTC midnight, so it formats in UTC or readers west of Greenwich see the day before. A timestamp
// keeps the reader's zone.

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

function pair(options: Intl.DateTimeFormatOptions) {
	return {
		utc: new Intl.DateTimeFormat('en-GB', { ...options, timeZone: 'UTC' }),
		local: new Intl.DateTimeFormat('en-GB', options)
	};
}

const DAY_MONTH_YEAR = pair({ day: 'numeric', month: 'short', year: 'numeric' });
const DAY_MONTH = pair({ day: 'numeric', month: 'short' });

function format(
	style: ReturnType<typeof pair>,
	value: string | null | undefined,
	empty: string
): string {
	if (typeof value !== 'string' || value.trim() === '') return empty;
	const t = Date.parse(value);
	if (Number.isNaN(t)) return empty;
	return (DATE_ONLY.test(value.trim()) ? style.utc : style.local).format(t);
}

/** An ISO date or datetime as `5 Aug 2026`. */
export function formatDate(value: string | null | undefined, empty = '—'): string {
	return format(DAY_MONTH_YEAR, value, empty);
}

/** The same without the year (`5 Aug`). */
export function formatDayMonth(value: string | null | undefined, empty = '—'): string {
	return format(DAY_MONTH, value, empty);
}

const TIME = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' });

/** A timestamp as `5 Aug 2026, 14:03`; a date-only value reads as `formatDate` does. */
export function formatDateTime(value: string | null | undefined, empty = '—'): string {
	const date = formatDate(value, '');
	if (!date) return empty;
	if (DATE_ONLY.test((value as string).trim())) return date;
	return `${date}, ${TIME.format(Date.parse(value as string))}`;
}
