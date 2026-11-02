/**
 * The client portal's real display helpers ($lib/format.ts).
 *
 * The countdown helpers are the point of this file. They used to live in `$lib/demo` and subtract
 * from `DEMO_NOW` — a hardcoded 2026-06-22 — while being rendered against a share link's own
 * `expiresAt` (routes/documents/+page.svelte). That is silently wrong rather than visibly broken:
 * the number looks plausible and is off by however far the real date has drifted from the fixture
 * clock. The "measured against the real clock" tests below fail against that version.
 */

import { describe, expect, it } from 'vitest';
import { daysUntil, daysUntilDue, daysUntilExpiry, formatDateTime } from './format';

const DAY_MS = 86_400_000;
const inDays = (n: number, from: Date = new Date()): string =>
	new Date(from.getTime() + n * DAY_MS).toISOString();

describe('countdowns are measured against the real clock', () => {
	it('counts forward from today, not from a fixture date', () => {
		// No injected `now`: this is exactly how the page calls it.
		expect(daysUntilExpiry(inDays(30))).toBe(30);
		expect(daysUntilDue(inDays(7))).toBe(7);
		expect(daysUntil(inDays(1))).toBe(1);
	});

	it('goes negative once the date is past', () => {
		expect(daysUntilExpiry(inDays(-3))).toBe(-3);
		expect(daysUntilDue(inDays(-1))).toBe(-1);
	});

	it('treats a link with no expiry as "never", not as "expired"', () => {
		expect(daysUntilExpiry(null)).toBeNull();
	});

	it('rounds a part-day up, so "expires in 12 hours" reads as 1 day, never 0', () => {
		const now = new Date('2026-07-28T09:00:00Z');
		expect(daysUntilExpiry('2026-07-28T21:00:00Z', now)).toBe(1);
		expect(daysUntilExpiry('2026-07-28T09:00:00Z', now)).toBe(0);
	});

	it('accepts an injected clock, so a test never depends on the day it runs', () => {
		const now = new Date('2026-07-28T12:00:00Z');
		expect(daysUntilExpiry('2026-08-27T12:00:00Z', now)).toBe(30);
		expect(daysUntilDue('2026-07-29T12:00:00Z', now)).toBe(1);
	});
});

describe('formatters', () => {
	it('renders a timestamp in British form', () => {
		expect(formatDateTime('2026-06-22T13:04:00Z')).toMatch(/^22 Jun 2026, \d{2}:\d{2}$/);
	});
});
