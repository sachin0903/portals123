import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { formatArchiveDayHeading, groupNotificationsByDay } from './day-groups.js';

describe('formatArchiveDayHeading', () => {
	const now = new Date('2026-09-09T12:00:00Z');

	it('labels the same calendar day as Today', () => {
		expect(formatArchiveDayHeading('2026-09-09T08:00:00Z', now)).toBe('Today');
	});

	it('labels the previous calendar day as Yesterday', () => {
		expect(formatArchiveDayHeading('2026-09-08T08:00:00Z', now)).toBe('Yesterday');
	});

	it('gives the rest of the last week a weekday + date, unlike the plain messaging heading', () => {
		// 3 days back — messaging's own formatDayLabel would fall straight to the long absolute date
		// here; the archive takes the Slack-style middle step instead.
		expect(formatArchiveDayHeading('2026-09-06T08:00:00Z', now)).toBe('Sunday, 6 September');
	});

	it('falls back to an absolute date beyond the last week', () => {
		expect(formatArchiveDayHeading('2026-08-26T08:00:00Z', now)).toBe('26 August 2026');
	});

	describe('local midnight boundary', () => {
		const originalTz = process.env.TZ;
		beforeAll(() => {
			process.env.TZ = 'Europe/London';
		});
		afterAll(() => {
			process.env.TZ = originalTz;
		});

		it('reads an early-morning local timestamp as Today, not a UTC-slice Yesterday', () => {
			// BST is UTC+1 in September, so 00:30 local on the 9th is 23:30 UTC on the 8th. A
			// `.toISOString().slice(0, 10)`-style UTC calendar slice would read that as the 8th — a
			// full day into "Yesterday" — even though the reader's own wall clock already reads the 9th.
			const localNow = new Date('2026-09-09T19:00:00+01:00');
			const earlyLocal = '2026-09-08T23:30:00Z';
			expect(formatArchiveDayHeading(earlyLocal, localNow)).toBe('Today');
		});
	});
});

describe('groupNotificationsByDay', () => {
	const now = new Date('2026-09-09T12:00:00Z');
	const item = (uid: string, created_at: string) => ({ uid, created_at });

	it('groups consecutive same-day rows under one heading', () => {
		const groups = groupNotificationsByDay(
			[
				item('a', '2026-09-09T09:00:00Z'),
				item('b', '2026-09-09T08:00:00Z'),
				item('c', '2026-09-08T08:00:00Z')
			],
			now
		);
		expect(groups.map((g) => [g.heading, g.items.length])).toEqual([
			['Today', 2],
			['Yesterday', 1]
		]);
	});

	it('repeats the heading for a non-adjacent same-day run rather than merging it', () => {
		const groups = groupNotificationsByDay(
			[
				item('a', '2026-09-09T09:00:00Z'),
				item('b', '2026-09-08T08:00:00Z'),
				item('c', '2026-09-09T07:00:00Z')
			],
			now
		);
		expect(groups.map((g) => g.heading)).toEqual(['Today', 'Yesterday', 'Today']);
	});

	it('returns [] for no items', () => {
		expect(groupNotificationsByDay([], now)).toEqual([]);
	});
});
