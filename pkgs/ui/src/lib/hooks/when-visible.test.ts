/**
 * `use:whenVisible` — the observer behind "reaching the bottom loads the next page".
 *
 * Pinned as a unit because the two properties that matter are invisible in a rendered page: that
 * it reports LEAVING as well as entering (which is what lets a caller stop retrying a failed page
 * without stalling for ever), and that it degrades to a no-op where there is no
 * IntersectionObserver rather than throwing and taking the list with it.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { whenVisible } from './when-visible.js';

type Cb = (entries: { isIntersecting: boolean }[]) => void;

/** A stand-in observer that hands the test its callback and records what it was asked to watch. */
function stubObserver() {
	const state: {
		callback?: Cb;
		options?: IntersectionObserverInit;
		observed: unknown[];
		disconnected: number;
	} = { observed: [], disconnected: 0 };
	class Stub {
		constructor(cb: Cb, options?: IntersectionObserverInit) {
			state.callback = cb;
			state.options = options;
		}
		observe(node: unknown) {
			state.observed.push(node);
		}
		unobserve() {}
		disconnect() {
			state.disconnected += 1;
		}
		takeRecords() {
			return [];
		}
	}
	vi.stubGlobal('IntersectionObserver', Stub);
	return state;
}

afterEach(() => vi.unstubAllGlobals());

const node = () => ({}) as HTMLElement;

describe('whenVisible', () => {
	it('reports entering and leaving, so a caller can tell the two apart', () => {
		const seen: boolean[] = [];
		const observer = stubObserver();
		whenVisible(node(), { onchange: (v) => seen.push(v) });

		observer.callback?.([{ isIntersecting: true }]);
		observer.callback?.([{ isIntersecting: false }]);
		expect(seen).toEqual([true, false]);
	});

	it('takes the LAST entry of a burst as the current state', () => {
		// A scroll can deliver several records for one node in one callback; the earliest is the
		// oldest reading, so acting on it would report a state the element has already left.
		const seen: boolean[] = [];
		const observer = stubObserver();
		whenVisible(node(), { onchange: (v) => seen.push(v) });

		observer.callback?.([{ isIntersecting: true }, { isIntersecting: false }]);
		expect(seen).toEqual([false]);
	});

	it('ignores an empty callback rather than guessing', () => {
		const onchange = vi.fn();
		const observer = stubObserver();
		whenVisible(node(), { onchange });
		observer.callback?.([]);
		expect(onchange).not.toHaveBeenCalled();
	});

	it('watches against the VIEWPORT, never a scroll container', () => {
		// The rule this protects: the audit list scrolls its own box from `md` up and lets the page
		// scroll below that. An element root does not scroll at narrow widths, so everything inside
		// it would intersect immediately and the list would page itself to the cap unread.
		const observer = stubObserver();
		whenVisible(node(), { onchange: () => {} });
		expect(observer.options?.root).toBe(null);
		expect(observer.options?.rootMargin).toBe('300px 0px');
	});

	it('takes a caller-chosen band', () => {
		const observer = stubObserver();
		whenVisible(node(), { onchange: () => {}, rootMargin: '0px' });
		expect(observer.options?.rootMargin).toBe('0px');
	});

	it('swaps the callback on update without re-arming the observer', () => {
		// A fresh observer would fire "entered" again for a node that has not moved, which on this
		// surface is a second page nobody asked for.
		const first = vi.fn();
		const second = vi.fn();
		const observer = stubObserver();
		const action = whenVisible(node(), { onchange: first });

		action.update({ onchange: second });
		observer.callback?.([{ isIntersecting: true }]);

		expect(first).not.toHaveBeenCalled();
		expect(second).toHaveBeenCalledWith(true);
		expect(observer.observed).toHaveLength(1);
	});

	it('disconnects when the node goes', () => {
		const observer = stubObserver();
		whenVisible(node(), { onchange: () => {} }).destroy();
		expect(observer.disconnected).toBe(1);
	});

	it('is a no-op where the browser has no observer, never a throw', () => {
		// The affordance it rides on is a real button, so the surface still works by hand. Throwing
		// here would take down the whole list for a browser that simply cannot pre-fetch.
		vi.stubGlobal('IntersectionObserver', undefined);
		const action = whenVisible(node(), { onchange: () => {} });
		expect(() => action.update({ onchange: () => {} })).not.toThrow();
		expect(() => action.destroy()).not.toThrow();
	});
});
