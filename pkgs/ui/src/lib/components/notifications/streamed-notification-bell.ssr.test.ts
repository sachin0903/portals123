import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import StreamedNotificationBell from './streamed-notification-bell.svelte';
import type { NotificationItem } from './describe.js';

/**
 * The server half of the streamed bell: which of its two shapes the HTML carries.
 *
 * A layout that streams its seed must get the PLACEHOLDER — the live bell seeds its "already
 * announced" set from the rows it mounts with, so mounting it empty and handing it the rows later
 * would pop the whole backlog as cards. A layout that still passes plain values (the client portal)
 * must get the bell it always had, with no placeholder flash. The hand-over itself needs a mounted
 * component and a resolved promise, which SSR has neither of.
 */
const item: NotificationItem = {
	uid: 'note_1',
	event_type: 'task.assigned',
	category: 'tasks',
	title: 'Review the application',
	body: null,
	link: '/tasks/task_1',
	priority: 'normal',
	subject_type: 'task',
	subject_id: 'task_1',
	read_at: null,
	created_at: '2026-08-24T09:00:00Z'
};

describe('StreamedNotificationBell (SSR)', () => {
	it('holds the place for a streamed seed — the same box, nothing to click', () => {
		const body = render(StreamedNotificationBell, {
			props: {
				items: new Promise<NotificationItem[]>(() => {}),
				unreadCount: new Promise<number>(() => {})
			}
		}).body;
		expect(body).toContain('data-notification-bell-placeholder');
		expect(body).toContain('size-8');
		expect(body).not.toContain('aria-label="Notifications');
	});

	it('holds the place when only one of the two is still streaming', () => {
		// Half a seed is no seed: the count without the rows (or the rows without the count) would
		// mount a bell that announces a backlog it cannot show, or shows rows under a badge of nought.
		const body = render(StreamedNotificationBell, {
			props: { items: [item], unreadCount: new Promise<number>(() => {}) }
		}).body;
		expect(body).toContain('data-notification-bell-placeholder');
		expect(body).not.toContain('aria-label="Notifications');
	});

	it('renders the live bell at once for values already in hand', () => {
		const body = render(StreamedNotificationBell, {
			props: { items: [item], unreadCount: 1 }
		}).body;
		expect(body).not.toContain('data-notification-bell-placeholder');
		expect(body).toContain('aria-label="Notifications (1 unread)"');
	});

	it('renders the live bell, empty, when given nothing at all', () => {
		// The defaults are the live bell's own: a caller that passes no seed is not streaming.
		const body = render(StreamedNotificationBell, { props: {} }).body;
		expect(body).not.toContain('data-notification-bell-placeholder');
		expect(body).toContain('aria-label="Notifications"');
	});
});
