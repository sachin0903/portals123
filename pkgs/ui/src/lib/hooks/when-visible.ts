/**
 * `use:whenVisible` — tell the caller when an element enters or leaves the viewport.
 *
 * Built for the bottom of a paged list, so a trail is read by scrolling rather than by pressing a
 * button in the middle of that gesture. It REPORTS visibility and does not fetch — the paging
 * rules stay in the component that owns them.
 *
 * Two choices that are not obvious. The root is the VIEWPORT, never the scroll container: this
 * surface scrolls its own box from `md` up and the PAGE below that, and the inner element would
 * make everything inside it intersect at once, paging to the cap without the reader moving. And it
 * fires on LEAVE as well as enter, which lets a failed page stop retrying until the reader returns.
 *
 * No `IntersectionObserver` ⇒ a no-op: the affordance it rides on is a real button.
 */
export interface WhenVisibleOptions {
	/** Called with `true` when the node enters the band, `false` when it leaves. */
	onchange: (visible: boolean) => void;
	/** How far ahead of the node to fire, as an IntersectionObserver `rootMargin`. The default
	 *  reaches roughly a screen before the sentinel, so the next page is usually in hand. */
	rootMargin?: string;
}

const DEFAULT_MARGIN = '300px 0px';

export function whenVisible(node: HTMLElement, options: WhenVisibleOptions) {
	let current = options;

	if (typeof IntersectionObserver === 'undefined') {
		// No observer (an old browser, a test environment): the caller's button is the fallback, and
		// it is a real control rather than a progressive-enhancement stub.
		return {
			update(next: WhenVisibleOptions) {
				current = next;
			},
			destroy() {}
		};
	}

	const observer = new IntersectionObserver(
		(entries) => {
			// The LAST entry is the current state — a burst can carry several for one node.
			const entry = entries[entries.length - 1];
			if (entry) current.onchange(entry.isIntersecting);
		},
		{ root: null, rootMargin: options.rootMargin ?? DEFAULT_MARGIN }
	);
	observer.observe(node);

	return {
		update(next: WhenVisibleOptions) {
			// Only the callback is swapped: `rootMargin` is fixed at construction by the observer's own
			// API, and re-creating one would fire a fresh "entered" for a node that never moved.
			current = next;
		},
		destroy() {
			observer.disconnect();
		}
	};
}
