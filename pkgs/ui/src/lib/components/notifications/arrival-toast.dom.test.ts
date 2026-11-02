/**
 * @vitest-environment jsdom
 *
 * The arrival pop-up, end to end: a notification the page has not seen before produces a card, and
 * one it has already seen does not.
 *
 * Mounted rather than asserted on `arrivals()` alone, because the decision is only half of it. The
 * other half is a chain of steps that are each invisible when broken — the bell has to refetch the
 * ROWS and not just the count (the push is contentless by design, so the content has to be asked
 * for), sonner has to render a `component` toast, and a `<Toaster />` has to be mounted to host it.
 * Every one of those failures leaves the badge and the dropdown working perfectly, which is why
 * `arrivals.test.ts` passing is not evidence that anything pops.
 *
 * `fetch` is stubbed rather than mocked at the module boundary: the bell talks to a same-origin
 * proxy the app owns, so the wire shape (`{items: [...]}`) is part of the contract under test.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import type { NotificationItem } from './describe.js';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let toast: typeof import('svelte-sonner').toast;
let NotificationBell: Component<Record<string, unknown>>;
let Toaster: Component<Record<string, unknown>>;

beforeAll(async () => {
	// jsdom gaps that bits-ui and sonner reach for. Assigned rather than `vi.stubGlobal`ed: these are
	// permanent scaffolding, and `vi.unstubAllGlobals()` in afterEach (which exists to restore
	// `fetch`) would take them away after the first case.
	globalThis.ResizeObserver ??= class {
		observe() {}
		unobserve() {}
		disconnect() {}
	} as unknown as typeof ResizeObserver;
	Element.prototype.scrollIntoView ??= () => {};
	// sonner animates its cards in, and watches `prefers-color-scheme`; jsdom has neither.
	Element.prototype.animate ??= (() => ({
		cancel() {},
		finish() {},
		addEventListener() {},
		removeEventListener() {}
	})) as unknown as Element['animate'];
	window.matchMedia ??= ((query: string) => ({
		matches: false,
		media: query,
		onchange: null,
		addEventListener() {},
		removeEventListener() {},
		addListener() {},
		removeListener() {},
		dispatchEvent: () => false
	})) as unknown as typeof window.matchMedia;

	({ mount, unmount, flushSync } = await import('svelte'));
	({ toast } = await import('svelte-sonner'));
	NotificationBell = (await import('./notification-bell.svelte'))
		.default as typeof NotificationBell;
	Toaster = (await import('../ui/sonner/sonner.svelte')).default as typeof Toaster;
	// Cold vite transform of bits-ui + sonner beats the 10s default hook timeout on Windows.
}, 120_000);

afterEach(async () => {
	// sonner's toast list is MODULE state that survives unmounting the Toaster, so a card raised
	// by one case renders into the next case's Toaster. Ordering the cases was enough while
	// removal was immediate; svelte-sonner 1.2 DEFERS it (`scheduleRemoval`), so dismissing alone
	// leaves the row in the store and the next case counts it.
	//
	// The order here is load-bearing: wait for sonner to actually empty BEFORE clearing the DOM.
	// Clearing first unmounts the Toast components, and their pending removal then never runs --
	// which is the leak this exists to close, one step later.
	toast.dismiss();
	await vi.waitFor(() => expect(cards()).toHaveLength(0));
	await vi.waitFor(() => expect(toast.getActiveToasts()).toHaveLength(0));
	document.body.innerHTML = '';
	vi.unstubAllGlobals();
});

function notification(uid: string, title: string): NotificationItem {
	return {
		uid,
		event_type: 'task.assigned',
		category: 'tasks',
		title,
		body: 'Review the suitability report',
		link: `/tasks/${uid}`,
		priority: 'normal',
		subject_type: null,
		subject_id: null,
		read_at: null,
		created_at: new Date().toISOString()
	};
}

/**
 * A stand-in for the browser's EventSource, which jsdom does not have.
 *
 * The bell refetches its rows when the stream PUSHES, and that is the path these cases need:
 * OPENING the dropdown also refetches, but it now suppresses pop-ups (it shows the same rows, so
 * a card over the panel hides what it is announcing) — so a harness built on opening the panel
 * cannot observe an arrival at all. Driving the stream is also the more honest test: a card pops
 * because something ARRIVED, not because somebody clicked the bell — and not because the stream
 * connected, either: the initial 'open' no longer refetches (the loader just seeded the bell),
 * which `the stream's connect and reconnect` below pins.
 */
class FakeEventSource {
	static last: FakeEventSource | null = null;
	listeners = new Map<string, ((event?: unknown) => void)[]>();
	constructor(readonly url: string) {
		FakeEventSource.last = this;
	}
	addEventListener(type: string, fn: (event?: unknown) => void) {
		this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
	}
	removeEventListener() {}
	close() {}
	/** Fire one stream event, as the server would. */
	emit(type: string, event: unknown = {}) {
		for (const fn of this.listeners.get(type) ?? []) fn(event);
	}
}

/** The contentless frame the API's stream sends — the browser is told THAT something changed. */
const push = () => ({ data: JSON.stringify({ type: 'notification' }) });

/** Mount a Toaster and a bell wired to a stubbed feed endpoint and a fake stream. The dropdown is
 *  left CLOSED; `arrive()` connects the stream and pushes once, which is what makes the bell
 *  refetch. */
function mountBell(
	feed: NotificationItem[],
	seeded: NotificationItem[] = [],
	opts: { toastDurationMs?: number } = {}
) {
	// Typed as fetch so the reconnect case can read what URL was asked for.
	const fetch = vi.fn<typeof globalThis.fetch>(
		async () => new Response(JSON.stringify({ items: feed }), { status: 200 })
	);
	vi.stubGlobal('fetch', fetch);
	vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
	FakeEventSource.last = null;
	const target = document.createElement('div');
	document.body.append(target);
	const toaster = mount(Toaster, { target: document.body, props: {} });
	const bell = mount(NotificationBell, {
		target,
		props: {
			items: seeded,
			feedEndpoint: '/notifications/recent',
			realtimeUrl: '/notifications/events',
			...opts
		}
	});
	flushSync();

	/** Let the stubbed fetch, if one was started, resolve into the bell. */
	async function settle() {
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();
	}

	/** Let the stream connect and push once — the push triggers the refetch the arrival rides in on. */
	async function arrive() {
		FakeEventSource.last?.emit('open');
		FakeEventSource.last?.emit('message', push());
		await settle();
	}

	/** Click the bell's trigger — the panel, not the stream. */
	function toggle() {
		target.querySelector<HTMLElement>('[aria-label^="Notifications"]')!.click();
		flushSync();
	}

	return {
		arrive,
		settle,
		fetch,
		stream: () => FakeEventSource.last!,
		toggle,
		done: () => {
			unmount(bell);
			unmount(toaster);
		}
	};
}

/** Mount, let one batch arrive over the stream, and hand back the controls. */
async function arrivesOver(
	feed: NotificationItem[],
	seeded: NotificationItem[] = [],
	opts: { toastDurationMs?: number } = {}
) {
	const bell = mountBell(feed, seeded, opts);
	await bell.arrive();
	return bell;
}

const cards = () => document.querySelectorAll('[data-notification-toast]');

describe('the stream’s connect and reconnect', () => {
	it('does not refetch on the initial connect — the loader has just seeded the bell', async () => {
		// The rows and the count the bell mounts with ARE the server's answer from a moment ago;
		// asking for both again the instant the stream connects was two duplicate round trips on
		// every page load, and exactly the work the page's "settle" was left waiting on.
		const bell = mountBell(
			[notification('fresh-1', 'Arrived')],
			[notification('seen-1', 'Seeded')]
		);
		bell.stream().emit('open');
		await bell.settle();
		expect(bell.fetch).not.toHaveBeenCalled();
		bell.done();
	});

	it('refetches on a RECONNECT — that is what closes the gap a dropped connection leaves', async () => {
		// The feed answers the seeded row back, so nothing pops here: the first case of the next
		// block needs an empty toast store (see its own comment), and this runs before it.
		const seeded = [notification('seen-1', 'Seeded')];
		const bell = mountBell(seeded, seeded);
		bell.stream().emit('open');
		await bell.settle();
		// The connection dropped and came back: whatever was pushed in between was never heard.
		bell.stream().emit('open');
		await bell.settle();
		expect(bell.fetch).toHaveBeenCalledTimes(1);
		expect(String(bell.fetch.mock.calls[0][0])).toBe('/notifications/recent');
		bell.done();
	});
});

describe('a notification that arrives while the page is open pops a card', () => {
	// The negative case runs FIRST on purpose, and says so rather than relying on it silently.
	// sonner's toast list is module state with no public reset, and unmounting the Toaster does not
	// empty it — so a card raised by an earlier case would still be in the store and would render
	// into the next case's Toaster. The guard below turns that into a named failure instead of a
	// confusing one if this file is ever reordered.
	it('does not replay a notification that was already on screen', async () => {
		expect(
			toast.getActiveToasts(),
			'this case needs an empty toast store — it must run before any case that pops one'
		).toHaveLength(0);

		// The loader had already put this row up, so opening the bell is not an arrival. Without the
		// seeded `announced` set this pops on every popover open and every navigation.
		const seeded = [notification('seen-1', 'A task was assigned to you')];
		const { done } = await arrivesOver(seeded, seeded);

		expect(cards()).toHaveLength(0);
		done();
	});

	it('pops a card the page can click through, and dismiss', async () => {
		const { done } = await arrivesOver([notification('new-1', 'A task was assigned to you')]);

		await vi.waitFor(() => expect(cards()).toHaveLength(1));
		const card = cards()[0];
		expect(card.textContent).toContain('A task was assigned to you');
		expect(card.textContent).toContain('Review the suitability report');
		// Clicking it goes to the thing it is about.
		expect(card.getAttribute('href')).toBe('/tasks/new-1');
		// And it carries its own close control, because sonner suppresses its close button for a
		// `component` toast — so if this is ever dropped there is no way to get rid of the card.
		// "Close", not "Dismiss": shutting a pop-up is not a claim about the notification, which
		// stays unread and on the badge. "Dismiss" is the bell row's action, and it persists.
		expect(card.querySelector('[aria-label="Close"]')).not.toBeNull();
		// A lone card carries no bulk control — it would do exactly what its own X does.
		expect(card.querySelector('[aria-label="Close all notifications"]')).toBeNull();

		done();
	});

	it('offers "Close all" on a stack, on the front card only, and it shuts every card', async () => {
		const { done } = await arrivesOver([
			notification('batch-1', 'First arrival'),
			notification('batch-2', 'Second arrival')
		]);

		await vi.waitFor(() => expect(cards()).toHaveLength(2));
		const bulk = document.querySelectorAll('[aria-label="Close all notifications"]');
		// Exactly one, however many cards are up: the control belongs to the stack, not to a card,
		// and one per card would be three buttons doing the same thing.
		expect(bulk).toHaveLength(1);

		(bulk[0] as HTMLElement).click();
		// sonner removes the cards on its own schedule, hence waitFor rather than a bare assert.
		await vi.waitFor(() => expect(cards()).toHaveLength(0));

		done();
	});

	it('closes the cards when the dropdown opens, since it shows the same rows', async () => {
		const { toggle, done } = await arrivesOver([notification('open-1', 'An arrival')]);
		await vi.waitFor(() => expect(cards()).toHaveLength(1));

		toggle();

		// The panel lists this notification, so a card floating over it hides the very row it is
		// announcing — and the bell is behind it.
		await vi.waitFor(() => expect(cards()).toHaveLength(0));
		done();
	});

	it('raises nothing while the dropdown is open, and not afterwards either', async () => {
		const bell = mountBell([notification('quiet-1', 'Arrived while open')]);
		bell.toggle(); // open FIRST, then let the notification land
		await bell.arrive();

		expect(cards()).toHaveLength(0);

		// And it must not be merely deferred: the reader has seen it in the list, so closing the
		// panel is not the cue to pop a card about it.
		bell.toggle();
		await new Promise((r) => setTimeout(r, 0));
		flushSync();
		expect(cards()).toHaveLength(0);
		bell.done();
	});

	// A card must go on its own, and a stack must keep doing so after one of them is closed.
	//
	// This is the shape that broke: svelte-sonner 1.1.1 wrote a height at an index it had looked
	// up in the TOASTS array, so dismissing one card left a hole in `heights`, and the `$derived`
	// that reads it threw `Cannot read properties of undefined (reading 'toastId')`. A throw in
	// that derived takes the component's reactivity with it, and the survivors then sit there for
	// ever -- which reads as 'the toast never times out' rather than as a crash.
	//
	// A real (short) duration rather than fake timers: faking `setTimeout` freezes vitest's worker
	// heartbeat and the run dies with 'Timeout waiting for worker to respond' (see this file's
	// header, and the same warning in CLAUDE.md).
	it('closes each card on its own timer, and keeps doing so after one is dismissed', async () => {
		const { done } = await arrivesOver(
			[notification('timed-1', 'First arrival'), notification('timed-2', 'Second arrival')],
			[],
			{ toastDurationMs: 80 }
		);

		await vi.waitFor(() => expect(cards()).toHaveLength(2));

		// Close the front card by its own X -- the interaction that desynchronised the two arrays.
		// Asserted, not optional-chained: a selector that matches nothing makes the rest of this
		// case prove nothing while still passing, which is how it was first written.
		const close = document.querySelector<HTMLElement>('[aria-label="Close"]');
		expect(close, 'the card draws no Close control').not.toBeNull();
		close!.click();

		// Whatever that did, the rest must still expire. Before the fix the survivor stayed up.
		await vi.waitFor(() => expect(cards()).toHaveLength(0), { timeout: 4000 });

		done();
	});
});
