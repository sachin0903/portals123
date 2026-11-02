/**
 * Provenance stamps — "as at 29 Jul 2026" — for valuation-bearing surfaces. The API ships
 * date-only ISO stamps (`as_of`, `value_as_of`, `performance_period.{from,to}` on /crm/home;
 * see the provenance note in apps/api/app/routers/crm.py) and the portal renders them ONLY
 * when present: an absent stamp renders nothing, never a fabricated date.
 *
 * Junk-safe by contract: these values ride untyped JSON, so anything unparseable formats to
 * '' and the caller's `{#if}` naturally suppresses the stamp.
 */

import { formatDate } from '../../dates.js';

// Date-only by contract: a stamp carrying a time is read by its date part, in UTC.
const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

/** An ISO date as British display form ('2026-07-29' → '29 Jul 2026'); junk/absent → ''. */
export function britishDate(iso: string | null | undefined): string {
	if (typeof iso !== 'string' || !ISO_DATE.test(iso)) return '';
	// '' rather than an em dash: a dash dropped inside 'as at …' states a fact nobody has.
	return formatDate(iso.slice(0, 10), '');
}

/** The book as a count — '1 account', '12 accounts' — for the data half of a heading row. */
export function accountsOf(n: number): string {
	return `${n.toLocaleString('en-GB')} ${n === 1 ? 'account' : 'accounts'}`;
}

/** The full stamp phrase ('as at 29 Jul 2026'), or '' when there is nothing honest to say. */
export function asAt(iso: string | null | undefined): string {
	const d = britishDate(iso);
	return d ? `as at ${d}` : '';
}

/**
 * The real basis suffix for a period label ('to 29 Jul 2026') from the API's
 * `performance_period`. Only the series END matters here — the visible window is a client-side
 * slice of the returned series, so `from` describes the payload, not what is on screen.
 */
export function periodBasis(
	period: { from?: string | null; to?: string | null } | null | undefined
): string {
	const to = britishDate(period?.to);
	return to ? `to ${to}` : '';
}
