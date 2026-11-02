/** The two representations a date range lives in, and the conversion between them.
 *
 *  A listing stores its range as `YYYY-MM-DD` strings — in the URL, in a loader, in an API query.
 *  The range calendar speaks `@internationalized/date` values. Keeping the conversion here rather
 *  than inline in the component means both directions are asserted, and a caller that puts a range
 *  in the URL never has to learn the calendar's types.
 */
import { CalendarDate, type DateValue } from '@internationalized/date';

/** `YYYY-MM-DD` → a calendar date. Anything unparseable is ABSENT, not an error: the range arrives
 *  from a URL a person can edit by hand, and a typo there must narrow nothing rather than take the
 *  whole listing down with it. */
export function toCalendarDate(value: string | undefined | null): CalendarDate | undefined {
	if (!value) return undefined;
	const parts = value.split('-');
	if (parts.length !== 3) return undefined;
	const [y, m, d] = parts.map((part) => (/^\d+$/.test(part) ? Number(part) : NaN));
	if (y === undefined || m === undefined || d === undefined) return undefined;
	if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d)) return undefined;
	if (m < 1 || m > 12 || d < 1 || d > 31) return undefined;
	try {
		const date = new CalendarDate(y, m, d);
		// `CalendarDate` balances an impossible day (31 February becomes 3 March), which would hand
		// the reader back a date they did not type. Reject it instead.
		return date.month === m && date.day === d ? date : undefined;
	} catch {
		return undefined;
	}
}

/** A calendar date → `YYYY-MM-DD`, zero-padded so the strings sort and compare as dates do. */
export function toDateText(value: DateValue | undefined | null): string {
	if (!value) return '';
	return `${String(value.year).padStart(4, '0')}-${String(value.month).padStart(2, '0')}-${String(value.day).padStart(2, '0')}`;
}
