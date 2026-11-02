import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import NotificationFeed from './notification-feed.svelte';
import type { NotificationItem } from './describe.js';

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

describe('NotificationFeed pagination (SSR)', () => {
	it('renders one 25-row page with real previous and next links', () => {
		const body = render(NotificationFeed, {
			props: {
				items: [item],
				page: 2,
				hasMore: true,
				previousHref: '/notifications',
				nextHref: '?page=3'
			}
		}).body;

		expect(body).toContain('26–26');
		expect(body).toContain('href="/notifications"');
		expect(body).toContain('href="?page=3"');
		expect(body).not.toContain('Load more');
	});
});

describe('NotificationFeed reads like a Slack archive (SSR)', () => {
	it('stamps a grouped row with a clock time, not a relative age', () => {
		const body = render(NotificationFeed, { props: { items: [item] } }).body;

		// The day is already overhead in the divider, so repeating it in the row ("5d ago",
		// "24 Aug") says the same thing twice and less precisely. `dayGrouped` swaps it for the
		// time — the bell, which has no dividers, keeps the age.
		// The SHAPE, not a literal: the row renders in the reader's own timezone, so 09:00Z is
		// 10:00 wherever this runs in BST. Pinning the literal would make the suite pass or fail
		// on the machine's clock settings, which is the bug the helper itself takes care to avoid.
		expect(body).toMatch(/\d{2}:\d{2}/);
		expect(body).not.toMatch(/\d+d ago/);
	});

	it('draws the day divider as a pill that sticks while the day scrolls past', () => {
		const body = render(NotificationFeed, { props: { items: [item] } }).body;

		expect(body).toContain('24 August 2026');
		// Sticky WITH a top anchor, or a long day scrolls its own heading away and the rows lose
		// their date. The anchor's value is deliberately not pinned: it has been a plain offset, a
		// negative one, and is now the topbar token — what matters is that one exists.
		expect(body).toMatch(/sticky[^"]*top-/);
		// Opaque, because rows pass underneath it.
		expect(body).toContain('rounded-full');
	});
});

describe('NotificationFeed search and filters (SSR)', () => {
	it('shows no toolbar without onquery, rather than one that filters a single page', () => {
		const body = render(NotificationFeed, { props: { items: [item] } }).body;

		expect(body).not.toContain('Search notifications');
		expect(body).not.toContain('Filter by category');
	});

	it('renders the search box and the funnel when the app owns the navigation', () => {
		const body = render(NotificationFeed, {
			props: {
				items: [item],
				onquery: () => {},
				facets: { category: ['tasks'], priority: ['high'] }
			}
		}).body;

		expect(body).toContain('Search notifications');
		expect(body).toContain('aria-label="Show filters"');
	});

	it('says nothing MATCHES, not nothing exists, when a filter emptied the page', () => {
		// The two are different facts and only one of them is the reader's fault. An archive that
		// says "Nothing here yet" under an active filter reads as data loss.
		const body = render(NotificationFeed, {
			props: { items: [], onquery: () => {}, filters: { state: 'unread' } }
		}).body;

		expect(body).toContain('Nothing matches');
		expect(body).not.toContain('Nothing here yet');
	});
});
