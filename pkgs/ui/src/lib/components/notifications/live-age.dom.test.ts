/**
 * @vitest-environment jsdom
 *
 * The relative age on a notification must TICK — "just now" has to become "1m ago" on its own, with
 * no refresh and no tab-switch. That was fixed once (d0e2ce93, the shared `now.svelte.ts` clock) and
 * a second freeze went unnoticed on top of it, because every other test in this package is either a
 * pure-function test or an SSR render: neither can observe a value that only changes on a timer in a
 * mounted component. So this is one of the first CLIENT-mounted tests in `pkgs/ui`, and the missing
 * capability — not thin coverage — is why the bug shipped green twice.
 *
 * Two levels on purpose:
 *  - the ROW on its own, which pins the clock → `$derived` → text-node path;
 *  - the row inside an OPEN bell popover, which pins the same thing through the portal the real
 *    surface renders it in. A row that ticks in isolation and freezes in the dropdown is exactly the
 *    shape of the bug, and the row-level test alone cannot see it.
 *
 * The clock is a module SINGLETON shared by every test in this file, so each case measures from
 * `syncedNow()` rather than from a fixed constant — an assertion pinned to an absolute string would
 * pass or fail on test ORDER, which is how a real regression gets "fixed" by a reorder.
 * `clock-start.dom.test.ts` owns the one case that needs a virgin module.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import type { NotificationItem } from './describe.js';

const BASE = new Date('2026-08-21T12:00:00Z');
/** Must match `now.svelte.ts`'s TICK_MS — the cadence the clock actually publishes at. */
const TICK_MS = 10_000;

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let NotificationItemRow: Component<{ item: NotificationItem }>;
let NotificationBell: Component<{ items: NotificationItem[] }>;

beforeAll(async () => {
	// `toFake` is narrowed on purpose: faking `setTimeout` too would freeze vitest's own worker
	// heartbeat and the run dies with "Timeout waiting for worker to respond" rather than a test
	// failure. The clock under test uses `setInterval` + `Date`, so those two are all that is needed
	// — and bits-ui's floating layer keeps a real `setTimeout`, which it needs.
	vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
	vi.setSystemTime(BASE);
	// bits-ui's floating layer reaches for browser APIs jsdom does not implement. Stubbing them is
	// scaffolding for the popover, never part of what is under test.
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			unobserve() {}
			disconnect() {}
		}
	);
	if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

	({ mount, unmount, flushSync } = await import('svelte'));
	NotificationItemRow = (await import('./notification-item.svelte'))
		.default as typeof NotificationItemRow;
	NotificationBell = (await import('./notification-bell.svelte'))
		.default as typeof NotificationBell;
	// Generous: these imports pull bits-ui through vite's transform on a cold run, which takes
	// longer than the 10s default hook timeout on Windows.
}, 120_000);

afterEach(() => {
	document.body.innerHTML = '';
});

/** Advance to the clock's next published tick and return the instant it now reads. A notification
 *  stamped with this is exactly "just now" — no sub-tick lag to make an assertion flaky.
 *
 *  It lands ON the interval boundary rather than advancing a bare TICK_MS, because the clock only
 *  publishes when the interval FIRES. A case that advances a non-multiple of the tick (the 45s
 *  threshold one does) leaves the fake clock misaligned, and from there a bare tick fires the
 *  interval part-way through the window — so `current` sits up to a tick behind the stamp handed to
 *  the row, and every later assertion is short by that much. That reads as the label being late,
 *  which is the very bug under test: the helper must not be able to fake it either way. */
function syncedNow(): Date {
	vi.advanceTimersByTime(TICK_MS - (Date.now() % TICK_MS));
	return new Date();
}

/** Advance the wall clock AND the timers together — `vi.advanceTimersByTime` moves the faked system
 *  time too, so the clock's `new Date()` on each tick reads the advanced time. */
function tick(ms: number) {
	vi.advanceTimersByTime(ms);
	flushSync();
}

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
		created_at: created.toISOString()
	};
}

function mountRow(created: Date) {
	const target = document.createElement('div');
	document.body.append(target);
	const app = mount(NotificationItemRow, { target, props: { item: notification(created) } });
	flushSync();
	return { target, app };
}

describe('a notification row ages live', () => {
	it('goes from "just now" to "1m ago" to "10m ago" on its own', () => {
		const { target, app } = mountRow(syncedNow());
		expect(target.textContent).toContain('just now');

		tick(60_000);
		expect(target.textContent).toContain('1m ago');

		tick(9 * 60_000);
		expect(target.textContent).toContain('10m ago');

		unmount(app);
	});

	it('leaves "just now" within one tick of the 45s threshold', () => {
		// The complaint was latency as much as freeze: at the old 30s cadence a fresh notification
		// could read "just now" for 75 seconds, which is long enough to be reported as "it never
		// updates". The label must move within a tick of becoming untrue.
		const { target, app } = mountRow(syncedNow());
		tick(45_000 + TICK_MS);
		expect(target.textContent).not.toContain('just now');
		unmount(app);
	});

	it('never renders a negative age when the stamp is ahead of the reader', () => {
		// Server and browser clocks disagree; an unclamped age renders "-1m ago".
		const { target, app } = mountRow(new Date(syncedNow().getTime() + 50_000));
		expect(target.textContent).toContain('just now');
		expect(target.textContent).not.toContain('-1m');
		unmount(app);
	});

	it('carries the exact timestamp as a hover title', () => {
		const created = syncedNow();
		const { target, app } = mountRow(created);
		const stamp = target.querySelector('[title]');
		expect(stamp?.getAttribute('title')).toBe(created.toLocaleString('en-GB'));
		unmount(app);
	});
});

describe('the bell dropdown ages live', () => {
	it('keeps ticking while the popover is open', async () => {
		const created = syncedNow();
		const target = document.createElement('div');
		document.body.append(target);
		const app = mount(NotificationBell, { target, props: { items: [notification(created)] } });
		flushSync();

		const trigger = target.querySelector<HTMLElement>('[aria-label^="Notifications"]');
		expect(trigger, 'the bell trigger renders').not.toBeNull();
		trigger!.click();
		flushSync();
		await vi.advanceTimersByTimeAsync(0);
		flushSync();

		// The popover portals to document.body, so read the whole document, not `target`.
		expect(document.body.textContent).toContain('just now');

		tick(60_000);
		expect(document.body.textContent).toContain('1m ago');

		unmount(app);
	});
});

// LAST on purpose. `vi.setSystemTime` moves the wall clock without advancing the timer queue, so it
// leaves the interval's phase offset from `Date.now()` — every later case would then measure ages a
// few seconds short and fail on a boundary, which reads as a product bug rather than test bleed.
describe('the clock survives a throttled or sleeping tab', () => {
	it('re-reads the wall clock when the tab is looked at again', () => {
		const { target, app } = mountRow(syncedNow());
		expect(target.textContent).toContain('just now');

		// A hidden tab has its timers throttled and a sleeping machine does not run them at all. Move
		// the wall clock WITHOUT letting the interval fire — that is what waking up looks like.
		vi.setSystemTime(new Date(Date.now() + 40 * 60_000));
		flushSync();
		expect(target.textContent, 'no tick has fired yet').toContain('just now');

		window.dispatchEvent(new Event('focus'));
		flushSync();
		expect(target.textContent).toContain('40m ago');

		unmount(app);
	});
});
