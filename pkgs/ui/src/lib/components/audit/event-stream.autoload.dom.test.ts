/**
 * Reaching the bottom of the trail loads the next page — no press.
 *
 * A trail is read by SCROLLING, so a button at the bottom is a stop sign in the middle of the one
 * gesture the surface is used with. The button stays (no observer, a failed page, a keyboard), but
 * arriving is enough. Every rule here is run-time and invisible to a server render, so this mounts
 * the real component, drives the IntersectionObserver by hand and asserts on what the fetcher was
 * asked for.
 *
 * The three that are not obvious, each a real hazard:
 *
 *   * a SHORT page keeps going. An observer fires on a CHANGE of intersection, so a page whose rows
 *     do not push the sentinel off screen never fires twice — hence an effect over visibility.
 *   * a FAILED page stops, or a failing endpoint is hammered in a tight loop from every reader's
 *     browser at once.
 *   * the CAP holds. Auto-paging must not walk past the in-place limit the button honoured.
 *
 * No fake timers (the pkgs/ui note in CLAUDE.md: faking `setTimeout` kills vitest's worker
 * heartbeat and the run reads as a hang).
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import type { ActionEvent } from './describe.js';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let tick: typeof import('svelte').tick;
let EventStream: Component<Record<string, unknown>>;

/** Every observer the component made, with the callback so a test can move the sentinel. */
type Watcher = { callback: (entries: { isIntersecting: boolean }[]) => void; node: Element };
let watchers: Watcher[] = [];

beforeAll(async () => {
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			unobserve() {}
			disconnect() {}
		}
	);
	vi.stubGlobal(
		'IntersectionObserver',
		class {
			#callback: Watcher['callback'];
			constructor(cb: Watcher['callback']) {
				this.#callback = cb;
			}
			observe(node: Element) {
				watchers.push({ callback: this.#callback, node });
			}
			unobserve() {}
			disconnect() {
				watchers = watchers.filter((w) => w.callback !== this.#callback);
			}
			takeRecords() {
				return [];
			}
		}
	);
	if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

	({ mount, unmount, flushSync, tick } = await import('svelte'));
	EventStream = (await import('./event-stream.svelte')).default as typeof EventStream;
}, 120_000);

afterEach(() => {
	document.body.innerHTML = '';
	watchers = [];
});

const event = (id: number): ActionEvent => ({
	id,
	event_type: 'client.created',
	created_at: '2026-08-03T09:15:00Z',
	method: 'POST',
	route: '/firm/contacts',
	status: 200,
	path_params: {},
	metadata: { correlation_id: `corr_${id}` },
	actor_id: `u-${id}`,
	actor_sub: `wos_${id}`,
	actor_email: 'a@firm.test',
	actor_name: 'Alice Feed',
	tenant_id: 1,
	tenant_uid: 'ten_firm',
	tenant_name: 'Feed Firm',
	target_tenant_id: null,
	target_tenant_uid: null,
	target_tenant_name: null
});

const page = (ids: number[], hasMore = true) => ({ events: ids.map(event), hasMore });

function mounted(onLoadMore: (before: number) => unknown, events = [event(30), event(29)]) {
	const target = document.createElement('div');
	document.body.append(target);
	const component = mount(EventStream, {
		target,
		props: { events, hasMore: true, onLoadMore }
	});
	return {
		target,
		rows: () => target.querySelectorAll('tbody tr[aria-expanded]').length,
		// BY ITS TEXT, not the first button in the tree — the toolbar above the table has eleven of
		// them, and `querySelector('button')` quietly returns a filter trigger.
		button: () =>
			[...target.querySelectorAll<HTMLElement>('button')].find((b) =>
				/Load more|Loading/.test(b.textContent ?? '')
			),
		dispose: () => unmount(component)
	};
}

/**
 * Let every pending effect and awaited page settle.
 *
 * Repeated rather than a fixed pair of ticks: an auto-page CHAINS — the effect re-runs when the
 * load clears and asks again — so one round trip per cycle, and a short-page run legitimately does
 * three before `hasMore` goes false. A fixed number of ticks asserts on however far the chain
 * happened to get, which is a test that passes for the wrong reason.
 */
async function settle() {
	for (let i = 0; i < 12; i += 1) {
		flushSync();
		await tick();
	}
}

/** Move the sentinel into (or out of) the band and let the effects settle. */
async function sentinel(visible: boolean) {
	// Flush FIRST: Svelte runs a `use:` action in a post-mount effect, so straight after `mount()`
	// the observer does not exist yet and a test that reached for it found nothing. (That is not a
	// component bug — it is the same one frame of delay a real browser has.)
	await settle();
	// The LAST watcher is the sentinel's: it is the only observed node in this component, and it is
	// re-armed whenever the load-more block is rebuilt.
	watchers.at(-1)?.callback([{ isIntersecting: visible }]);
	await settle();
}

describe('reaching the bottom pages the trail', () => {
	it('loads the next page with no press at all', async () => {
		const asked: number[] = [];
		const ui = mounted((before) => {
			asked.push(before);
			return page([28, 27], false);
		});
		expect(asked).toEqual([]); // nothing on arrival — the reader has not reached the bottom

		await sentinel(true);

		// Asked from the OLDEST row on screen (the keyset cursor), and the rows landed.
		expect(asked).toEqual([29]);
		expect(ui.rows()).toBe(4);
		ui.dispose();
	});

	it('keeps going when a page is too short to push the sentinel away', async () => {
		// THE STALL THIS RULES OUT. An IntersectionObserver fires on a CHANGE of intersection, so a
		// sentinel that is still visible after the page lands never fires again — driven off the
		// observer alone the list would stop dead until the reader nudged it by a pixel.
		const asked: number[] = [];
		const ui = mounted((before) => {
			asked.push(before);
			return page([before - 1], before > 27);
		});

		await sentinel(true);

		expect(asked).toEqual([29, 28, 27]); // …and it stopped when `hasMore` went false
		ui.dispose();
	});

	it('does not page because a row was opened', async () => {
		// THE LAG SPIKE. Opening a row moves everything below it, so the sentinel can slide into the
		// prefetch band with the reader sitting still — and the page it fetched was fifty more rows
		// and a full re-render, one click after the click. Auto-paging is armed by SCROLLING.
		const asked: number[] = [];
		const ui = mounted((before) => {
			asked.push(before);
			return page([28], false);
		});
		await settle();

		const row = ui.target.querySelectorAll<HTMLElement>('tbody tr[aria-expanded]')[1];
		row?.click();
		await sentinel(true);
		expect(asked).toEqual([]);

		// …and a real scroll asks immediately: the sentinel never moved, only the reader's intent.
		window.dispatchEvent(new Event('scroll'));
		await settle();
		expect(asked).toEqual([29]);
		ui.dispose();
	});

	it('stops asking after a page that failed, until the reader comes back', async () => {
		// A failing endpoint must not be hammered in a loop from every reader's browser: the sentinel
		// is still on screen and `more` is still true, so nothing else would stop it.
		const asked: number[] = [];
		let fail = true;
		const ui = mounted((before) => {
			asked.push(before);
			return fail ? null : page([28], false);
		});

		await sentinel(true);
		expect(asked).toEqual([29]); // one attempt, not a loop

		// Still sitting at the bottom: still nothing.
		await sentinel(true);
		expect(asked).toEqual([29]);

		// Scrolling AWAY and back is the reader asking again — the cheapest retry there is.
		fail = false;
		await sentinel(false);
		await sentinel(true);
		expect(asked).toEqual([29, 29]);
		expect(ui.rows()).toBe(3);
		ui.dispose();
	});

	it('leaves the button working as the way back from a failure', async () => {
		const asked: number[] = [];
		let fail = true;
		const ui = mounted((before) => {
			asked.push(before);
			return fail ? null : page([28], false);
		});

		await sentinel(true);
		expect(asked).toEqual([29]);

		fail = false;
		const button = ui.button();
		expect(button).toBeTruthy();
		button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await settle();

		expect(asked).toEqual([29, 29]);
		expect(ui.rows()).toBe(3);
		ui.dispose();
	});

	it('never pages while the reader is above the bottom', async () => {
		const asked: number[] = [];
		const ui = mounted((before) => {
			asked.push(before);
			return page([28], true);
		});
		await sentinel(false);
		expect(asked).toEqual([]);
		ui.dispose();
	});
});
