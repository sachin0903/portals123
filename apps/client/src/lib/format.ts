// Real, shared presentation helpers — safe to use on a signed-in client's own data.
//
// These used to live in `$lib/demo` alongside the fixture data, which meant importing
// `formatDate` from `$lib/demo` normalised that module and made real-vs-fabricated invisible at
// the call site. They are pure formatters with no fixture dependency: nothing here reads the
// fixed demo "now", so a date or size shown through these is the real one.
//
// The countdown helpers live here too, and measure against the REAL clock. They used to sit in
// `$lib/demo` and subtract from `DEMO_NOW` (a hardcoded 2026-06-22), which is the worst kind of
// wrong: `daysUntilExpiry` is rendered on a share link's own `expiresAt`, so as soon as real share
// links reach that loader every countdown would be silently mis-stated rather than visibly broken
// (docs/audit/06-core.md → "Seed / demo data strategy").

// Dates and sizes are the shared formatters. These names stay so the client pages that already
// call `formatDate` / `formatSize` do not grow a second implementation.
export { formatDate } from '@graphene/ui';
export { formatBytes as formatSize } from '@graphene/ui';

/** A timestamp as "22 Jun 2026, 13:04", in the same day style as `formatDate`. */
export function formatDateTime(iso: string): string {
	return new Date(iso).toLocaleString('en-GB', {
		day: 'numeric',
		month: 'short',
		year: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});
}

const DAY_MS = 86_400_000;

/** Whole days from now until `iso`, rounded up: 0 = today, negative = already past. `now` is
 *  injectable for tests only — callers pass nothing and get the real clock. */
export function daysUntil(iso: string, now: Date = new Date()): number {
	return Math.ceil((new Date(iso).getTime() - now.getTime()) / DAY_MS);
}

/** Days until a share link expires; `null` when the link never expires. */
export function daysUntilExpiry(expiresAt: string | null, now: Date = new Date()): number | null {
	return expiresAt === null ? null : daysUntil(expiresAt, now);
}

/** Days until an action item is due (negative once overdue). */
export function daysUntilDue(dueDate: string, now: Date = new Date()): number {
	return daysUntil(dueDate, now);
}
