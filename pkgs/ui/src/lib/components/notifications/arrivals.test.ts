import { describe, expect, it } from 'vitest';
import { arrivals, MAX_TOASTS_PER_BATCH } from './arrivals.js';
import type { NotificationItem } from './describe.js';

const BASE = Date.parse('2026-08-21T12:00:00Z');

function item(uid: string, minutesAgo = 0, read = false): NotificationItem {
	return {
		uid,
		event_type: 'task.assigned',
		category: 'tasks',
		title: `Notification ${uid}`,
		body: null,
		link: null,
		priority: 'normal',
		subject_type: null,
		subject_id: null,
		read_at: read ? new Date(BASE).toISOString() : null,
		created_at: new Date(BASE - minutesAgo * 60_000).toISOString()
	};
}

describe('arrivals — which notifications pop', () => {
	it('pops an unread one it has not seen before', () => {
		const { toast, seen } = arrivals([item('a')], new Set());
		expect(toast.map((n) => n.uid)).toEqual(['a']);
		expect(seen).toEqual(['a']);
	});

	it('never pops the same notification twice', () => {
		const feed = [item('a')];
		const first = arrivals(feed, new Set());
		const second = arrivals(feed, new Set(first.seen));
		expect(second.toast).toEqual([]);
		// The refetch happens on every push, every popover open and every loader re-run, so this is
		// the common case, not the edge one.
		expect(second.seen).toEqual([]);
	});

	it('does not replay the backlog that was already on screen', () => {
		// What signing in looks like: the loader put five rows up, and none of them are arrivals.
		const feed = [item('a'), item('b'), item('c'), item('d'), item('e')];
		const { toast } = arrivals(feed, new Set(feed.map((n) => n.uid)));
		expect(toast).toEqual([]);
	});

	it('ignores ones already read', () => {
		// Read in another tab, or read here and re-listed by a refetch that landed after the
		// optimistic update. Neither is an arrival.
		const { toast, seen } = arrivals([item('a', 0, true), item('b')], new Set());
		expect(toast.map((n) => n.uid)).toEqual(['b']);
		expect(seen).toEqual(['a', 'b']); // both are dealt with — 'a' must not come back as fresh
	});

	it('caps a burst and still marks every one of it seen', () => {
		// A flow completing or a nightly run can deliver a dozen at once. A dozen stacked cards is a
		// modal dialog nobody asked for; the badge and the list are where a backlog belongs.
		const burst = Array.from({ length: 12 }, (_, i) => item(`n${i}`, i));
		const { toast, seen } = arrivals(burst, new Set());
		expect(toast).toHaveLength(MAX_TOASTS_PER_BATCH);
		expect(seen).toHaveLength(12);
		// The three MOST RECENT, not the first three the API happened to return.
		expect(toast.map((n) => n.uid).sort()).toEqual(['n0', 'n1', 'n2']);
	});

	it('raises them oldest first, so the newest ends up on top of the stack', () => {
		const { toast } = arrivals([item('newest', 0), item('older', 5), item('oldest', 9)], new Set());
		expect(toast.map((n) => n.uid)).toEqual(['oldest', 'older', 'newest']);
	});

	it('picks the newest by timestamp, not by the order it was handed', () => {
		// The cap decides which of a burst get dropped, and "the wrong three" has no symptom — just
		// the wrong notifications on screen.
		const shuffled = [item('old', 30), item('new', 1), item('mid', 10), item('older', 60)];
		const { toast } = arrivals(shuffled, new Set(), 2);
		expect(toast.map((n) => n.uid)).toEqual(['mid', 'new']);
	});

	it('pops nothing when the cap is zero, but still marks them seen', () => {
		const { toast, seen } = arrivals([item('a')], new Set(), 0);
		expect(toast).toEqual([]);
		expect(seen).toEqual(['a']);
	});
});

describe('arrivals — what the reader is already looking at', () => {
	const item = (uid: string, over: Partial<NotificationItem> = {}): NotificationItem => ({
		uid,
		event_type: 'message.sent',
		category: 'messages',
		title: 'New message',
		body: null,
		link: '/chats',
		priority: 'normal',
		subject_type: 'contact',
		subject_id: '42',
		read_at: null,
		created_at: '2026-08-24T09:00:00Z',
		...over
	});

	it('does not pop what the caller says is on screen', () => {
		const { toast, silence } = arrivals([item('a')], new Set(), 3, (n) => n.uid === 'a');
		expect(toast).toEqual([]);
		expect(silence.map((n) => n.uid)).toEqual(['a']);
	});

	it('still pops everything else in the same batch', () => {
		const out = arrivals(
			[item('a'), item('b', { subject_id: '99' })],
			new Set(),
			3,
			(n) => n.uid === 'a'
		);
		expect(out.toast.map((n) => n.uid)).toEqual(['b']);
		expect(out.silence.map((n) => n.uid)).toEqual(['a']);
	});

	it('partitions BEFORE the cap, so a burst cannot leave the read one unread', () => {
		// Silenced items are marked read by the caller. If the cap ran first, three louder arrivals
		// could push the one you are looking at out of `silence` — and it would sit unread on the
		// badge, which is the exact false claim this exists to remove.
		const loud = ['b', 'c', 'd', 'e'].map((u) => item(u, { subject_id: '99' }));
		const out = arrivals([item('a'), ...loud], new Set(), 3, (n) => n.uid === 'a');
		expect(out.toast).toHaveLength(3);
		expect(out.silence.map((n) => n.uid)).toEqual(['a']);
	});

	it('silences nothing when no predicate is given', () => {
		const out = arrivals([item('a')], new Set());
		expect(out.silence).toEqual([]);
		expect(out.toast.map((n) => n.uid)).toEqual(['a']);
	});

	it('never silences an already-read notification', () => {
		// `silence` is a list to MARK READ. One already read has nothing to mark, and re-marking it
		// would decrement a badge that never counted it.
		const out = arrivals(
			[item('a', { read_at: '2026-08-24T09:01:00Z' })],
			new Set(),
			3,
			() => true
		);
		expect(out.silence).toEqual([]);
		expect(out.seen).toEqual(['a']);
	});
});
