import { describe, expect, it } from 'vitest';
import {
	formatDayLabel,
	formatListTime,
	groupMessagesByDay,
	groupMessagesIntoRuns,
	type ApiGroupFeedMessage,
	type ApiMessage
} from './messages';

describe('formatDayLabel', () => {
	const now = new Date('2026-08-04T12:00:00Z');

	it('labels the same calendar day as "Today"', () => {
		expect(formatDayLabel('2026-08-04T18:00:00Z', now)).toBe('Today');
	});

	it('labels the previous calendar day as "Yesterday"', () => {
		expect(formatDayLabel('2026-08-03T12:00:00Z', now)).toBe('Yesterday');
	});

	it('falls back to a long date further back', () => {
		expect(formatDayLabel('2026-03-14T10:00:00Z', now)).toBe('14 March 2026');
	});
});

describe('formatListTime', () => {
	const now = new Date('2026-08-04T12:00:00Z');

	it('shows a bare time for today', () => {
		expect(formatListTime('2026-08-04T09:15:00Z', now)).toMatch(/09:15|10:15/);
	});

	it('shows "Yesterday" for the previous day', () => {
		expect(formatListTime('2026-08-03T09:15:00Z', now)).toBe('Yesterday');
	});

	it('shows a short date further back', () => {
		expect(formatListTime('2026-03-14T09:15:00Z', now)).toBe('14 Mar');
	});

	it('is blank when there is no last message', () => {
		expect(formatListTime(null, now)).toBe('');
	});

	it('reads "Now" for anything in the last five minutes', () => {
		// A clock time on a message that landed thirty seconds ago makes the reader do the
		// arithmetic to find out whether it is new. Five minutes is the window the thread already
		// uses to decide two messages are one turn.
		expect(formatListTime('2026-08-04T11:59:30Z', now)).toBe('Now');
		expect(formatListTime('2026-08-04T11:55:30Z', now)).toBe('Now');
		expect(formatListTime('2026-08-04T11:54:00Z', now)).not.toBe('Now');
	});

	it('reads "Now" for a message the server stamped a few seconds ahead', () => {
		// The browser's clock ticks every ten seconds and is never exactly the server's, so a
		// message sent THIS second routinely arrives "in the future". A floor at zero made the row
		// show a clock time for one tick before flipping to "Now" — visible on every send.
		expect(formatListTime('2026-08-04T12:00:09Z', now)).toBe('Now');
		expect(formatListTime('2026-08-04T12:00:30Z', now)).toBe('Now');
	});

	it('does not call a badly WRONG clock "Now"', () => {
		// A minute of tolerance swallows the tick and ordinary skew; a clock hours out is a clock
		// to disbelieve, not a message that just landed.
		expect(formatListTime('2026-08-04T12:30:00Z', now)).not.toBe('Now');
	});
});

describe('groupMessagesByDay', () => {
	const now = new Date('2026-08-04T12:00:00Z');
	const msg = (id: number, created_at: string): ApiMessage => ({
		id,
		conversation_id: 1,
		conversation_uid: 'cnv_1',
		sender_kind: 'staff',
		sender_user_id: null,
		sender_name: null,
		sender_role: null,
		body: String(id),
		created_at,
		attachments: [],
		request_item: null
	});

	it('groups consecutive same-day messages under one heading', () => {
		const groups = groupMessagesByDay(
			[
				msg(1, '2026-08-04T09:00:00Z'),
				msg(2, '2026-08-04T10:00:00Z'),
				msg(3, '2026-08-03T09:00:00Z')
			],
			now
		);
		expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday']);
		expect(groups[0].items.map((m) => m.id)).toEqual([1, 2]);
		expect(groups[1].items.map((m) => m.id)).toEqual([3]);
	});

	it('repeats a heading for a non-adjacent run rather than merging it into an earlier group', () => {
		const groups = groupMessagesByDay(
			[
				msg(1, '2026-08-04T09:00:00Z'),
				msg(2, '2026-08-03T09:00:00Z'),
				msg(3, '2026-08-04T09:00:00Z')
			],
			now
		);
		expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday', 'Today']);
	});

	it('also groups a group feed (a different message shape entirely) by day', () => {
		const feedMsg = (id: number, created_at: string): ApiGroupFeedMessage => ({
			id,
			conversation_id: 1,
			conversation_uid: 'cnv_1',
			sender_kind: 'staff',
			sender_user_id: null,
			sender_name: null,
			sender_role: null,
			body: String(id),
			created_at,
			attachments: [],
			request_item: null,
			recipients: [{ contact_id: 10, contact_name: 'Alice' }]
		});
		const groups = groupMessagesByDay(
			[feedMsg(1, '2026-08-04T09:00:00Z'), feedMsg(2, '2026-08-03T09:00:00Z')],
			now
		);
		expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday']);
	});
});

describe('groupMessagesIntoRuns', () => {
	const at = (created_at: string, sender_kind: string, sender_user_id: string | null = 'u-1') => ({
		created_at,
		sender_kind,
		sender_user_id
	});

	it('joins consecutive messages from one person sent close together', () => {
		// The whole point: five one-word replies in the same minute are one turn, not five headers.
		const runs = groupMessagesIntoRuns([
			at('2026-08-04T12:00:00Z', 'staff'),
			at('2026-08-04T12:00:30Z', 'staff'),
			at('2026-08-04T12:02:00Z', 'staff')
		]);
		expect(runs).toHaveLength(1);
		expect(runs[0]).toHaveLength(3);
	});

	it('breaks a run when the sender changes', () => {
		const runs = groupMessagesIntoRuns([
			at('2026-08-04T12:00:00Z', 'staff'),
			at('2026-08-04T12:00:10Z', 'client', null),
			at('2026-08-04T12:00:20Z', 'staff')
		]);
		expect(runs.map((r) => r.length)).toEqual([1, 1, 1]);
	});

	it('breaks a run between two staff who merely sent at the same moment', () => {
		// Grouped on the user id, not the display name: two colleagues can share a name, and merging
		// them would put one person's words under the other's.
		const runs = groupMessagesIntoRuns([
			at('2026-08-04T12:00:00Z', 'staff', 'u-1'),
			at('2026-08-04T12:00:05Z', 'staff', 'u-2')
		]);
		expect(runs).toHaveLength(2);
	});

	it('breaks a run when the gap is long enough to deserve its own timestamp', () => {
		const runs = groupMessagesIntoRuns([
			at('2026-08-04T12:00:00Z', 'staff'),
			at('2026-08-04T12:06:00Z', 'staff')
		]);
		expect(runs).toHaveLength(2);
	});

	it('keeps a message with an unreadable timestamp in its own run', () => {
		// NaN comparisons are false, so a bad date starts a run rather than silently joining
		// whatever came before it and inheriting its header.
		const runs = groupMessagesIntoRuns([
			at('2026-08-04T12:00:00Z', 'staff'),
			at('not a date', 'staff')
		]);
		expect(runs).toHaveLength(2);
	});

	it('returns nothing for an empty thread', () => {
		expect(groupMessagesIntoRuns([])).toEqual([]);
	});
});
