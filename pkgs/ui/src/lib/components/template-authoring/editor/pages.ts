/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Multi-page helpers — the canvas stacks each `.page` (doc-page) vertically and
 * scrolls between them. We track which page is currently in view (the "active"
 * page) so the layers panel / page settings follow the scroll, and provide
 * smooth scroll-to-page navigation. Ported from the reference's rAF scroll-detection
 * loop and `scrollToPageByIdx`.
 */

/** The names the EDITOR mints, dropped from the rail's target strip.
 *
 * That strip says what a style rule will MATCH, so it may only name classes an author could write
 * one against. `page` is the shell every page shares and `pg-…` is the per-page isolation class
 * `assignPageClass` derives from a component id — it is REPLACED on clone, so a rule written
 * against it is void on the next duplicate. Left in, selecting a page reported three targets where
 * the author had none, and the two internals crowded out the one thing the strip is for.
 *
 * Matched exactly, never by prefix: `page-header` is a perfectly ordinary class for an author to
 * add, and a `^page` test would silently swallow it.
 */
export function authoredClasses(classes: readonly string[]): string[] {
	return classes.filter((c) => c !== 'page' && !c.startsWith('pg-'));
}

/** Give a page its own class so per-page styling (background colour/image) targets THAT page
 *  (`.page.pg-…`) instead of the shared `.page` — which otherwise leaks e.g. a cover image onto
 *  every page. Any existing `pg-*` is replaced first, so a freshly-cloned page (clone copies the
 *  source's classes) can be re-isolated with an id of its own. */
export function assignPageClass(comp: any): void {
	try {
		for (const c of comp.getClasses?.() ?? [])
			if (typeof c === 'string' && c.startsWith('pg-')) comp.removeClass(c);
		comp.addClass(`pg-${comp.getId?.() ?? Math.random().toString(36).slice(2, 8)}`);
	} catch {
		/* editor not ready */
	}
}

/** Ensure every page carries its own styling class (migrates templates built before per-page
 *  classes existed). Idempotent — a page that already has a `pg-*` class is left untouched. */
export function ensurePageClasses(editor: any): void {
	for (const p of getPageComponents(editor)) {
		const has = (p.getClasses?.() ?? []).some(
			(c: any) => typeof c === 'string' && c.startsWith('pg-')
		);
		if (!has) assignPageClass(p);
	}
}

/** Duplicate the page at `index`, inserting the copy immediately after it. */
export function duplicatePage(editor: any, index: number): void {
	try {
		const wrapper = editor.getWrapper?.();
		if (!wrapper) return;
		const pages = getPageComponents(editor);
		if (index < 0 || index >= pages.length) return;
		const comp = pages[index];
		// GrapesJS's native clone() deep-clones the full component tree: each node
		// gets a fresh ccid, classes are copied as name strings (not live Selector
		// models), inline styles and traits are copied, and any ID-scoped CSS rules
		// are duplicated with new selectors. This is correct; toJSON()+add() was
		// lossy because toJSON returns a shallow clone whose `components`/`classes`
		// attributes are still live Backbone collections, not plain arrays.
		const cloned = comp.clone();
		// Use the real wrapper-children index, not the filtered page index.
		const insertAt = wrapper.components().indexOf(comp) + 1;
		wrapper.components().add(cloned, { at: insertAt });
		// The clone copied the source's `pg-*` class — re-isolate the duplicate so styling one page
		// doesn't bleed onto the other.
		assignPageClass(cloned);
	} catch {
		/* editor not ready */
	}
}

/** Move the page at `fromIndex` to `toIndex`. */
export function movePage(editor: any, fromIndex: number, toIndex: number): void {
	if (fromIndex === toIndex) return;
	try {
		const wrapper = editor.getWrapper?.();
		if (!wrapper) return;
		const pages = getPageComponents(editor);
		if (fromIndex < 0 || toIndex < 0 || fromIndex >= pages.length || toIndex >= pages.length)
			return;
		const comp = pages[fromIndex];
		const target = pages[toIndex];
		// Deep clone then drop the original — the reliable path (see duplicatePage). `toJSON()` was
		// LOSSY here: its nested `components` came back as a live Backbone collection rather than
		// plain data, so the re-inserted page rendered EMPTY — the "reorder wiped a page" bug.
		// clone() copies the whole subtree, inline styles, traits and the page's own `pg-*` styling
		// class, so both content and appearance survive the move (the original is removed, so its
		// copied class can't leak).
		const cloned = comp.clone();
		const at = wrapper.components().indexOf(target) + (toIndex > fromIndex ? 1 : 0);
		wrapper.components().add(cloned, { at });
		comp.remove();
	} catch {
		/* editor not ready */
	}
}

/** The ordered `.page` components in the document. */
export function getPageComponents(editor: any): any[] {
	const wrapper = editor.getWrapper?.();
	if (!wrapper) return [];
	return wrapper.components().filter((c: any) => c.getClasses?.().includes('page'));
}

/** Smooth-scroll the canvas to the page at `index`. */
export function scrollToPage(editor: any, index: number): void {
	try {
		const doc = editor.Canvas?.getDocument?.() as Document | null;
		if (!doc) return;
		const el = Array.from(doc.querySelectorAll('.page'))[index] as HTMLElement | undefined;
		// scrollIntoView scrolls whichever element is the real scroll container (the iframe's html,
		// its body, or an outer canvas wrapper — it varies with the zoom/layout overrides). The old
		// hand-computed documentElement.scrollTo silently no-oped when the scrolling happened
		// elsewhere: the page counter moved but the canvas didn't.
		el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	} catch {
		/* canvas not ready */
	}
}

/**
 * Watch the canvas scroll position and report the most-visible page (its index and
 * the total page count) whenever either changes. A rAF loop — bypasses scroll-event
 * timing issues with the canvas iframe. Returns a stop function.
 */
export function watchActivePage(
	editor: any,
	onChange: (index: number, total: number) => void
): () => void {
	let lastIdx = -1;
	let lastScrollTop = -1;
	let lastTotal = -1;
	let rafId = 0;

	const tick = () => {
		try {
			const doc = editor.Canvas?.getDocument?.() as Document | null;
			if (doc) {
				const html = doc.documentElement;
				const pages = Array.from(doc.querySelectorAll('.page')) as HTMLElement[];
				const scrollTop = html.scrollTop;
				// Only recompute when something relevant moved.
				if (pages.length && (scrollTop !== lastScrollTop || pages.length !== lastTotal)) {
					lastScrollTop = scrollTop;
					const viewBot = scrollTop + html.clientHeight;
					const bodyOffset = doc.body.offsetTop;
					let bestIdx = 0;
					let bestVisible = -1;
					pages.forEach((el, i) => {
						const elTop = bodyOffset + el.offsetTop;
						const elBot = elTop + el.offsetHeight;
						const visible = Math.max(0, Math.min(viewBot, elBot) - Math.max(scrollTop, elTop));
						if (visible > bestVisible) {
							bestVisible = visible;
							bestIdx = i;
						}
					});
					if (bestIdx !== lastIdx || pages.length !== lastTotal) {
						lastIdx = bestIdx;
						lastTotal = pages.length;
						onChange(bestIdx, pages.length);
					}
				}
			}
		} catch {
			/* canvas not ready this frame */
		}
		rafId = requestAnimationFrame(tick);
	};

	rafId = requestAnimationFrame(tick);
	return () => cancelAnimationFrame(rafId);
}
