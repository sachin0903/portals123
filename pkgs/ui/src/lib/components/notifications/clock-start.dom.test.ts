/**
 * @vitest-environment jsdom
 *
 * THE bug, in its own file because it needs a VIRGIN clock module.
 *
 * `now.svelte.ts` seeds `current` at import and used to start its interval lazily, on the first
 * READ. Nothing reads it until a notification row renders — and a row renders only when the bell's
 * popover is opened, which may be hours after the page loaded. So on a long-lived tab the clock sat
 * frozen at page-load time, and the first open of the dropdown measured every notification against
 * a stale `now`:
 *
 *  - an item that arrived an hour ago read as "just now" (a future age, clamped);
 *  - an item from this morning read as the age it had when the page loaded.
 *
 * A refresh re-seeded the module and the times were right again — which is exactly the "it only
 * updates if I refresh" report. The lazy start also self-healed one tick later, so the window is
 * narrow and easy to dismiss as a rendering blip; it is not, and this pins it shut.
 *
 * The assertion is deliberately made with NOTHING mounted for the whole three hours: that is the
 * real condition (bell closed, no row in the DOM), and it is the one a test that mounts first can
 * never reproduce, because mounting is what used to start the clock.
 */
import { beforeAll, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import type { NotificationItem } from './describe.js';

const PAGE_LOAD = new Date('2026-08-21T09:00:00Z');

let mount: typeof import('svelte').mount;
let flushSync: typeof import('svelte').flushSync;
let NotificationItemRow: Component<{ item: NotificationItem }>;

beforeAll(async () => {
	// See live-age.dom.test.ts for why `toFake` is narrowed rather than faking everything.
	vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
	vi.setSystemTime(PAGE_LOAD);
	({ mount, flushSync } = await import('svelte'));
	NotificationItemRow = (await import('./notification-item.svelte'))
		.default as typeof NotificationItemRow;
}, 120_000);

function notification(created: Date): NotificationItem {
	return {
		uid: 'n1',
		event_type: 'task.assigned',
		category: 'tasks',
		title: 'A task was assigned to you',
		body: null,
		link: null,
		priority: 'normal',
		subject_type: null,
		subject_id: null,
		read_at: null,
		created_at: created.toISOString(),
		...{}
	};
}

it('is already current the first time a row renders, hours after page load', () => {
	// Three hours of a tab sitting open with the dropdown shut — nothing has read the clock.
	vi.advanceTimersByTime(3 * 60 * 60_000);

	// Now the bell is opened for the first time and a row renders. The notification landed one
	// hour ago; a clock frozen at page load would measure it against 09:00 and call it "just now".
	const target = document.createElement('div');
	document.body.append(target);
	mount(NotificationItemRow, {
		target,
		props: { item: notification(new Date(PAGE_LOAD.getTime() + 2 * 60 * 60_000)) }
	});
	flushSync();

	expect(target.textContent).toContain('1h ago');
	expect(target.textContent).not.toContain('just now');
});
