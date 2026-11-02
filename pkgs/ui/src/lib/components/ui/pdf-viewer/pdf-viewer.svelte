<script lang="ts">
	// A self-rendered PDF viewer: pdf.js draws each page onto a canvas, with a transparent text layer
	// over it for selection and search. Unlike an <iframe> to the browser's built-in PDF plugin there
	// is NO native chrome — no toolbar, no grey surround, no plugin scrollbar — so a document reads
	// like the rest of the app: white pages, capped to a readable width, our own scroll container.
	//
	// The worker URL is a static `?url` import (SSR-safe — just an asset path, served same-origin by
	// each app's Vite, deliberately not a CDN); the heavy pdf.js lib is dynamically imported inside
	// the render so it never enters the SSR/first-load bundle of a page that shows no PDF.
	//
	// PAGES RENDER LAZILY. Measuring is cheap (a page dictionary, no rasterising), so every page gets
	// a correctly-sized placeholder up front — the scroll height is right immediately and nothing
	// jumps — but a canvas and its text layer are built only for pages near the viewport, and
	// released once one is far away. Eager rasterising is what makes a naive pdf.js viewer fall over:
	// an A4 page at 860 CSS px and dpr 2 is a 1720×2432 bitmap, about 16 MB, so a 100-page pack would
	// ask the browser for well over a gigabyte before showing anything.
	//
	// SEARCH IS OURS, NOT THE BROWSER'S, and that follows directly from the laziness: the browser's
	// own find can only see text that is in the DOM, so on a 100-page document it would silently
	// report hits from the three pages that happen to be painted. Ours searches the extracted text of
	// EVERY page (`pdf-search.ts`), then scrolls to a hit and paints the page it is on. ⌘F is
	// intercepted only while the pointer or focus is inside the viewer — hijacking a browser shortcut
	// is justified when the browser's version of it would give a wrong answer, and nowhere else.
	//
	// ZOOM IS OURS TOO, and for the same shape of reason. The browser's own ⌘+ scales the painted
	// canvas, so it blurs the very page it is asked to enlarge; ours changes the render scale and
	// repaints, which is the only way to actually get more detail out of a vector page. A zoom is a
	// multiplier over the FITTED scale (`pdf-zoom.ts`), and the ONE invariant that matters is that a
	// sheet's box, its canvas and its text layer's `--total-scale-factor` all move together — that is
	// why nothing resizes a sheet except `rescale`.
	import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
	import type { PDFDocumentLoadingTask, PDFPageProxy, RenderTask, TextLayer } from 'pdfjs-dist';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronUpIcon from '@lucide/svelte/icons/chevron-up';
	import Maximize2Icon from '@lucide/svelte/icons/maximize-2';
	import Minimize2Icon from '@lucide/svelte/icons/minimize-2';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import PreviewSpinner from './preview-spinner.svelte';
	import { findMatches, type PdfMatch } from './pdf-search.js';
	import {
		MAX_ZOOM,
		MIN_ZOOM,
		anchorFraction,
		backingScale,
		clampZoom,
		fitScale,
		renderScale,
		restoreScrollTop,
		stepZoom,
		zoomLabel
	} from './pdf-zoom.js';
	import { Button } from '../button/index.js';
	import { Separator } from '../separator/index.js';
	import { cn } from '../../../utils.js';

	let {
		src,
		/** How many screens' worth either side of the viewport to keep painted. One screen is enough
		    that a normal scroll rarely catches an unpainted page; more costs memory for no gain. */
		overscan = 1,
		/** Set false for a preview pane where a find bar would be clutter (the template editor). The
		    text layer stays either way — selection is not the search bar's to withhold. */
		searchable = true,
		/** Set false to drop the zoom pill for the same reason — a small fixed preview pane where the
		    chrome would cost more than the control is worth. */
		zoomable = true,
		/** Whether the host has given the viewer the whole page. Display state only: the layout being
		    expanded belongs to whoever owns it (which sidebar to hide is not the viewer's business),
		    so this is what the toggle SHOWS and `onexpand` is how it asks. */
		expanded = false,
		/** Absent ⇒ no expand affordance. The host renders the toggle by accepting the request, the
		    portal-waterfall posture: an optional callback, and the control simply isn't there
		    without it. */
		onexpand,
		class: className = ''
	}: {
		src: string;
		overscan?: number;
		searchable?: boolean;
		zoomable?: boolean;
		expanded?: boolean;
		onexpand?: (next: boolean) => void;
		class?: string;
	} = $props();

	/** The outer box: the positioning context for the floating chrome, and the hover target. */
	let frame = $state<HTMLDivElement>();
	/** The inner box: what actually scrolls, and the IntersectionObserver's root. */
	let container = $state<HTMLDivElement>();
	let host = $state<HTMLDivElement>();
	let status = $state<'loading' | 'ready' | 'error'>('loading');
	// Plain (non-reactive) bookkeeping. `token` cancels a stale in-flight render; `renderedKey` skips
	// a redundant re-render of the same source at the same width (belt-and-braces against re-runs).
	let token = 0;
	let renderedKey = '';
	/** The LAYOUT generation, deliberately separate from `token`. A zoom invalidates every painted
	    page without touching the document or its search index — bumping `token` for it would abort
	    an in-flight `buildIndex`, which leaves the find bar saying "reading…" for ever. */
	let paintGen = 0;

	type Slot = {
		page: PDFPageProxy;
		index: number;
		scale: number;
		/** Last known intersection state, kept by the observer. A rescale has to repaint what is on
		    screen itself: the observer reports CHANGES, and a page that was visible before and after
		    reports nothing, so it would sit as a blank placeholder until the reader scrolled. */
		visible: boolean;
		task?: RenderTask;
		textLayer?: TextLayer;
	};
	let live: {
		/** The LOADING TASK, not the document: `destroy()` lives there and tears down both, and the
		    document has to stay alive for the whole session here — a lazily-painted page asks it for
		    bytes long after the first render finished. */
		task: PDFDocumentLoadingTask;
		observer: IntersectionObserver;
		slots: Map<Element, Slot>;
		/** Placeholders in page order, so a search hit can find the sheet it belongs to. */
		sheets: Element[];
		/** The widest page's width at scale 1 — kept so the fit can be recomputed when the pane
		    changes width (expanding) without re-reading every page dictionary. */
		maxWidth: number;
		/** The scale at which that widest page fits the pane. The rendered scale is this × `zoom`. */
		fit: number;
	} | null = null;

	const HOST_PADDING = 24; // the host's p-3 (12px each side)
	/** The names the two ::highlight() rules below are registered under. */
	const HL_ALL = 'pdf-find';
	const HL_CURRENT = 'pdf-find-current';

	/** The reader's zoom, as a multiplier over the fitted scale: 1 IS "fit the width", which is why
	    there is one reset rather than a separate "100%" (see `pdf-zoom.ts`). */
	let zoom = $state(1);

	// ---- search state -------------------------------------------------------------------------
	/** Every page's extracted text, in page order. Empty until the index finishes building. */
	let pageText = $state<string[]>([]);
	let findOpen = $state(false);
	let query = $state('');
	let matches = $state<PdfMatch[]>([]);
	let truncated = $state(false);
	let current = $state(0);
	let indexing = $state(false);
	/** Whether the pointer is over the viewer — half of the ⌘F guard (focus is the other half). */
	let hovered = false;
	let findInput = $state<HTMLInputElement>();

	function teardown(): void {
		clearHighlights();
		paintGen++; // nothing painted at the old layout may land afterwards
		if (!live) return;
		live.observer.disconnect();
		for (const slot of live.slots.values()) {
			slot.task?.cancel();
			slot.textLayer?.cancel();
		}
		void live.task.destroy();
		live = null;
		pageText = [];
		matches = [];
		truncated = false;
	}

	function clearHighlights(): void {
		if (typeof CSS === 'undefined' || !CSS.highlights) return;
		CSS.highlights.delete(HL_ALL);
		CSS.highlights.delete(HL_CURRENT);
	}

	/** Paint one placeholder's page — canvas first, then its text layer. `mine` is the LAYOUT
	    generation the caller read: a paint that finishes after a zoom is drawn at the wrong scale, and
	    its text layer would carry the wrong `--total-scale-factor`, which is a drifting highlight on
	    every hit rather than an obviously wrong picture. */
	async function paint(el: Element, mine: number): Promise<void> {
		const slot = live?.slots.get(el);
		if (!slot || slot.task || el.querySelector('canvas')) return;
		const cssViewport = slot.page.getViewport({ scale: slot.scale });
		// The backing store is capped at 2× AND at a pixel budget: zoom multiplies the CSS page size
		// and the bitmap is that squared, so a page at 300% would otherwise ask for a canvas the
		// browser may simply refuse — which shows as a blank sheet with nothing logged.
		const dpr = backingScale(cssViewport.width, cssViewport.height, window.devicePixelRatio || 1);
		const canvas = document.createElement('canvas');
		const bitmap = slot.page.getViewport({ scale: slot.scale * dpr });
		canvas.width = Math.floor(bitmap.width);
		canvas.height = Math.floor(bitmap.height);
		// The placeholder already carries the CSS size, so the canvas just fills it — the backing
		// store is dpr× bigger and scales down, which is what makes the text crisp.
		canvas.className = 'absolute inset-0 block h-full w-full';
		const ctx = canvas.getContext('2d');
		if (!ctx) return;
		const task = slot.page.render({ canvas, canvasContext: ctx, viewport: bitmap });
		slot.task = task;
		try {
			await task.promise;
			if (mine !== paintGen) return;
			el.replaceChildren(canvas);
			await addTextLayer(el, slot, cssViewport, mine);
		} catch {
			// A cancelled render is the NORMAL path when a page scrolls out mid-paint; leave the
			// placeholder empty so it simply repaints if it comes back.
		} finally {
			if (slot.task === task) slot.task = undefined;
		}
	}

	/** The transparent, selectable text over a painted page. pdf.js positions one span per text item
	    against CSS variables it expects on the container — see the `:global(.pdf-text-layer)` rules
	    below, which are the minimum subset of the vendor stylesheet that makes that work. */
	async function addTextLayer(
		el: Element,
		slot: Slot,
		viewport: ReturnType<PDFPageProxy['getViewport']>,
		mine: number
	): Promise<void> {
		const pdfjs = await import('pdfjs-dist');
		if (mine !== paintGen || !live?.slots.has(el)) return;
		const layer = document.createElement('div');
		layer.className = 'pdf-text-layer';
		// Read from the SLOT, which `rescale` updates before it repaints — the layer's geometry and the
		// canvas it sits over come from the same number by construction.
		layer.style.setProperty('--total-scale-factor', String(slot.scale));
		layer.style.setProperty('--scale-round-x', '1px');
		layer.style.setProperty('--scale-round-y', '1px');
		const textLayer = new pdfjs.TextLayer({
			textContentSource: slot.page.streamTextContent(),
			container: layer,
			viewport
		});
		slot.textLayer = textLayer;
		try {
			await textLayer.render();
			if (mine !== paintGen || !live?.slots.has(el)) return;
			el.appendChild(layer);
			// A hit on this page may have been waiting for it to exist.
			if (matches[current]?.page === slot.index) applyHighlights();
		} catch {
			// Cancelled with the page; nothing to clean up beyond the layer we never appended.
		}
	}

	/** Drop a page's bitmap and text once it is well out of view. The placeholder keeps its size, so
	    the scroll position is untouched — only the memory goes. */
	function release(el: Element): void {
		const slot = live?.slots.get(el);
		if (!slot) return;
		slot.task?.cancel();
		slot.task = undefined;
		slot.textLayer?.cancel();
		slot.textLayer = undefined;
		el.replaceChildren();
	}

	// ---- zoom ---------------------------------------------------------------------------------

	/** The sheet the reader is actually looking at: the one covering the middle of the pane. Used as
	    the anchor a rescale restores the scroll position against.

	    `offsetTop` is read against the scroller, which is why that element carries `relative` — it
	    makes the sheets' `offsetParent` the same box `scrollTop` is measured in, so the two numbers
	    are directly comparable rather than coincidentally equal. */
	function anchorSheet(): HTMLElement | null {
		if (!live || !container) return null;
		const middle = container.scrollTop + container.clientHeight / 2;
		for (const sheet of live.sheets) {
			const el = sheet as HTMLElement;
			if (el.offsetTop + el.offsetHeight >= middle) return el;
		}
		return (live.sheets.at(-1) as HTMLElement | undefined) ?? null;
	}

	/**
	 * Re-lay-out the document at a new scale, in place.
	 *
	 * The document, its pages and the search index are untouched — only the sheet boxes and their
	 * bitmaps change, which is the whole reason zoom doesn't go back through `render`: re-fetching and
	 * re-measuring a 100-page pack to make it 25% bigger would put a spinner over the page someone was
	 * reading and throw away the index behind the find bar.
	 *
	 * THIS IS THE ONLY PLACE A SHEET IS RESIZED, deliberately. A sheet's size, its canvas and its text
	 * layer's `--total-scale-factor` are three views of one number, and the failure mode of letting
	 * them disagree is not a broken picture but a working one whose selection and search highlights sit
	 * a few millimetres out — so the order here (invalidate, release, set the scale, resize, repaint)
	 * is what keeps them in step, rather than a convention to remember.
	 */
	async function rescale(next: number): Promise<void> {
		const c = container;
		if (!live || !c) return;
		paintGen++; // anything mid-paint is now at the wrong scale
		// Keep the reader's place. Scaling changes every page's height, so a scrollTop left alone lands
		// on a different part of a different page — on a long pack, somewhere else entirely.
		const anchor = anchorSheet();
		const fraction = anchor
			? anchorFraction(c.scrollTop, anchor.offsetTop, anchor.offsetHeight)
			: 0;
		for (const [element, slot] of live.slots) {
			release(element);
			slot.scale = next;
			const viewport = slot.page.getViewport({ scale: next });
			const el = element as HTMLElement;
			el.style.width = `${Math.floor(viewport.width)}px`;
			el.style.height = `${Math.floor(viewport.height)}px`;
		}
		if (anchor) c.scrollTop = restoreScrollTop(anchor.offsetTop, anchor.offsetHeight, fraction);
		// Repaint what is on screen ourselves: the observer only reports intersection CHANGES, and a
		// page visible both before and after reports nothing — it would sit blank until a scroll.
		const mine = paintGen;
		await Promise.all(
			[...live.slots].filter(([, slot]) => slot.visible).map(([element]) => paint(element, mine))
		);
		// The old text layers went with the repaint, and a Range into a detached node draws nothing —
		// so every highlight has to be rebuilt against the layers that exist now.
		applyHighlights();
	}

	/** Set the reader's zoom and apply it. A no-op at the same value, so a repeated key press or a
	    click on the readout at 100% doesn't repaint the document for nothing. */
	function setZoom(next: number): void {
		const z = clampZoom(next);
		if (z === zoom) return;
		zoom = z;
		if (live) void rescale(renderScale(live.fit, z));
	}

	/** Re-fit to the pane's current width, keeping the reader's zoom. */
	function refit(): void {
		const c = container;
		if (!live || !c) return;
		const fit = fitScale(live.maxWidth, c.clientWidth - HOST_PADDING);
		if (fit === live.fit) return;
		live.fit = fit;
		void rescale(renderScale(fit, zoom));
	}

	// ---- search -------------------------------------------------------------------------------

	/** Pull every page's text once, in the background. Extraction is far cheaper than rasterising,
	    and it has to cover the whole document — a search that only sees painted pages is the exact
	    failure we replaced the browser's find to avoid. */
	async function buildIndex(doc: Awaited<PDFDocumentLoadingTask['promise']>, mine: number) {
		indexing = true;
		const texts: string[] = [];
		try {
			for (let n = 1; n <= doc.numPages; n++) {
				const page = await doc.getPage(n);
				if (mine !== token) return;
				const content = await page.getTextContent();
				if (mine !== token) return;
				// Join with nothing: the TextLayer emits one span per item in the same order, so this
				// string's offsets line up with a walk of that layer's text nodes.
				texts.push(content.items.map((i) => ('str' in i ? i.str : '')).join(''));
			}
			if (mine !== token) return;
			pageText = texts;
			if (query) runSearch();
		} finally {
			if (mine === token) indexing = false;
		}
	}

	function runSearch(): void {
		const result = findMatches(pageText, query);
		matches = result.matches;
		truncated = result.truncated;
		current = 0;
		if (matches.length) void goToMatch(0);
		else clearHighlights();
	}

	/** Walk a text layer's text nodes and turn an offset span into a DOM Range. Returns null when the
	    page isn't painted, or when the span falls outside what was rendered. */
	function rangeFor(layer: Element, start: number, end: number): Range | null {
		const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
		const range = document.createRange();
		let at = 0;
		let started = false;
		for (let node = walker.nextNode(); node; node = walker.nextNode()) {
			const len = node.textContent?.length ?? 0;
			if (!started && at + len > start) {
				range.setStart(node, start - at);
				started = true;
			}
			if (started && at + len >= end) {
				range.setEnd(node, end - at);
				return range;
			}
			at += len;
		}
		return null;
	}

	/** Repaint the highlight layers from the current match set. Only painted pages can carry a Range,
	    which is fine: an unpainted page is off screen, and painting it calls back here. */
	function applyHighlights(): void {
		if (typeof CSS === 'undefined' || !CSS.highlights || typeof Highlight === 'undefined') return;
		clearHighlights();
		if (!live || !matches.length) return;
		const all: Range[] = [];
		const currentRanges: Range[] = [];
		for (let i = 0; i < matches.length; i++) {
			const m = matches[i];
			if (!m) continue;
			const sheet = live.sheets[m.page];
			const layer = sheet?.querySelector('.pdf-text-layer');
			if (!layer) continue;
			const range = rangeFor(layer, m.start, m.end);
			if (!range) continue;
			if (i === current) currentRanges.push(range);
			else all.push(range);
		}
		if (all.length) CSS.highlights.set(HL_ALL, new Highlight(...all));
		if (currentRanges.length) CSS.highlights.set(HL_CURRENT, new Highlight(...currentRanges));
	}

	/** Scroll a match into view, painting its page if the observer hasn't yet. */
	async function goToMatch(index: number): Promise<void> {
		if (!live || !matches.length) return;
		current = ((index % matches.length) + matches.length) % matches.length;
		const match = matches[current];
		const sheet = match && live.sheets[match.page];
		if (!sheet) return;
		sheet.scrollIntoView({ block: 'center', behavior: 'smooth' });
		await paint(sheet, paintGen); // no-op when already painted; the observer may not have fired yet
		applyHighlights();
	}

	function closeFind(): void {
		findOpen = false;
		query = '';
		matches = [];
		truncated = false;
		clearHighlights();
	}

	function openFind(): void {
		findOpen = true;
		// After the input exists. `requestAnimationFrame` rather than `tick()` so it also works when
		// the bar was already open and ⌘F is pressed again to re-focus.
		requestAnimationFrame(() => findInput?.select());
	}

	function onWindowKeydown(event: KeyboardEvent): void {
		if (!frame) return;
		const inViewer = hovered || frame.contains(document.activeElement);
		if (!inViewer) return;
		if (event.key === 'Escape') {
			// Both shortcuts are scoped to the viewer, so one Escape must have one meaning: shut the
			// transient tool first, and only collapse the layout when there is no tool to shut.
			if (searchable && findOpen) return void closeFind();
			if (expanded && onexpand) {
				event.preventDefault();
				return void onexpand(false);
			}
			return;
		}
		if (!(event.metaKey || event.ctrlKey)) return;
		if (searchable && event.key === 'f') {
			event.preventDefault();
			openFind();
			return;
		}
		// Intercepting the browser's own zoom is justified on exactly the grounds ⌘F is: the browser's
		// version scales the painted canvas, so it blurs the page it was asked to enlarge, where ours
		// re-renders it. `=` is the unshifted key `+` lives on.
		if (!zoomable) return;
		if (event.key === '+' || event.key === '=') {
			event.preventDefault();
			setZoom(stepZoom(zoom, 1));
		} else if (event.key === '-' || event.key === '_') {
			event.preventDefault();
			setZoom(stepZoom(zoom, -1));
		} else if (event.key === '0') {
			event.preventDefault();
			setZoom(1);
		}
	}

	async function render(url: string, width: number): Promise<void> {
		const key = `${url}@${Math.max(240, Math.round(width))}`;
		if (key === renderedKey) return; // already showing exactly this
		renderedKey = key;
		const mine = ++token;
		const el = host;
		if (!el) return;
		status = 'loading';
		// A new document opens fitted. Carrying the last one's 250% over would be a reader's first
		// sight of a document being a corner of its first page.
		zoom = 1;
		teardown();
		// The layout generation THIS render owns, snapshotted (teardown just bumped it). A later
		// teardown or zoom moves it on, and this render's own first paint then correctly does nothing.
		const gen = paintGen;
		el.replaceChildren();
		try {
			const pdfjs = await import('pdfjs-dist');
			pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
			const task = pdfjs.getDocument({ url });
			const doc = await task.promise;
			if (mine !== token) return void task.destroy();

			// Measure every page first, then lay them all out at ONE shared scale. A document can mix
			// page sizes (an A4 portrait page next to a landscape or larger one). Fitting each page to
			// the pane width independently would normalise them to the same width and destroy their
			// relative sizes — the landscape sheet would look no wider than the portrait one. Instead
			// fit the WIDEST page to the pane and use that scale for all, so every page keeps its true
			// size relative to the others: portrait pages sit proportionally narrower and centred.
			const pages: PDFPageProxy[] = [];
			let maxWidth = 0;
			for (let n = 1; n <= doc.numPages; n++) {
				const page = await doc.getPage(n);
				if (mine !== token) return void task.destroy();
				pages.push(page);
				const w = page.getViewport({ scale: 1 }).width;
				if (w > maxWidth) maxWidth = w;
			}
			const fit = fitScale(maxWidth, width); // widest page fits the pane; the rest keep proportion
			const scale = renderScale(fit, zoom);

			const slots = new Map<Element, Slot>();
			const sheets: Element[] = [];
			const observer = new IntersectionObserver(
				(entries) => {
					for (const entry of entries) {
						// `paintGen` read live, not captured: the observer outlives every zoom, and a
						// captured generation would make each of its paints a silent no-op afterwards.
						const slot = live?.slots.get(entry.target);
						if (slot) slot.visible = entry.isIntersecting;
						if (entry.isIntersecting) void paint(entry.target, paintGen);
						else release(entry.target);
					}
				},
				// The overscan band, measured against the scroll container. A page inside it is
				// painted; leaving it releases the bitmap.
				{ root: container, rootMargin: `${Math.round(overscan * 100)}% 0px` }
			);
			live = { task, observer, slots, sheets, maxWidth, fit };

			pages.forEach((page, index) => {
				const viewport = page.getViewport({ scale });
				const sheet = document.createElement('div');
				// Sized from the real page box, so the scroll height is correct before anything is
				// painted — no reflow as pages appear, and no scroll jumping under the reader.
				sheet.style.width = `${Math.floor(viewport.width)}px`;
				sheet.style.height = `${Math.floor(viewport.height)}px`;
				sheet.className =
					'relative mx-auto mb-4 block overflow-hidden rounded-sm shadow-sm ring-1 ring-foreground/10 last:mb-0';
				// Paper, not chrome: a PDF page keeps its printed colour whatever the app theme, so
				// the background is deliberately unthemed and set inline rather than via a colour
				// utility class (token-hygiene keeps those to branded CSS vars). It also means an
				// unpainted placeholder reads as a blank sheet rather than a hole.
				sheet.style.background = 'white';
				el.appendChild(sheet);
				slots.set(sheet, { page, index, scale, visible: index === 0 });
				sheets.push(sheet);
				observer.observe(sheet);
			});
			// The observer fires asynchronously, so paint page 1 directly — otherwise the pane sits on
			// the spinner for a frame with the document already parsed.
			const first = sheets[0];
			if (first) await paint(first, gen);
			if (mine === token) status = 'ready';
			// After first paint: the reader has something to look at while this walks the document.
			if (searchable) void buildIndex(doc, mine);
		} catch {
			if (mine === token) {
				status = 'error';
				renderedKey = ''; // allow a retry
			}
		}
	}

	// Render once when the source changes, measured after a frame so the container has its layout
	// width. Deliberately NO ResizeObserver on the element: rendering grows the content (and toggles
	// the scrollbar), which feeds straight back into an element observer and flickers — the initial
	// fit is enough. Each render already fits pages to the width it measured.
	$effect(() => {
		const c = container;
		if (!src || !c || !host) return;
		const raf = requestAnimationFrame(() => void render(src, c.clientWidth - HOST_PADDING));
		return () => {
			token++; // invalidate any in-flight render for the previous src
			cancelAnimationFrame(raf);
			teardown();
			renderedKey = '';
		};
	});

	// Expanding hides whatever the host had beside the viewer, so the pane genuinely changes width and
	// the fit has to be recomputed. This is NOT the element-resize case the missing ResizeObserver is
	// about: the width changed because the reader asked for it, once, and rendering into the new width
	// cannot change it back — there is no loop to feed. `void expanded` is the whole dependency; the
	// measuring happens in the frame after, when the layout has settled, and reads nothing reactive.
	$effect(() => {
		void expanded;
		const raf = requestAnimationFrame(() => refit());
		return () => cancelAnimationFrame(raf);
	});
</script>

<svelte:window onkeydown={onWindowKeydown} />

<!-- TWO boxes on purpose. The outer one is the positioning context for the floating chrome; the inner
     one scrolls the pages. They used to be one element, and an absolutely-positioned pill inside a
     scroll container scrolls WITH the content — so the find button slid off the top of the pane as
     soon as you read past page one, and a zoom pill would have gone the same way. The chrome now sits
     outside the scroller, where it stays put.

     `className` goes on the outer box because that is what callers size and border (`h-[26rem]
     rounded-md border`, `absolute inset-0`); the scroller fills it. `scrollbar-gutter: stable`
     reserves the vertical scrollbar's width up front so the page can't be rendered a hair too wide
     when the scrollbar appears; `overflow-x-hidden` is belt-and-braces. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	bind:this={frame}
	onpointerenter={() => (hovered = true)}
	onpointerleave={() => (hovered = false)}
	class={cn('relative overflow-hidden', className)}
>
	<!-- `h-full`, not `absolute inset-0`: every caller gives the outer box a definite height, and both
	     resolve to the same thing when one does — but a caller who forgot would get a viewer of height
	     zero from an absolutely-positioned scroller (nothing visible at all), where this one falls back
	     to growing with its content, which is what the single-element version did.

	     NO background of its own: the surround belongs to whoever mounts the viewer (the record page
	     wants the app's own backdrop to show through), and each page SHEET paints its own white paper
	     regardless. An opaque `bg-background` here silently overrode the `bg-transparent` its host was
	     already passing, so the pane read as a flat grey slab. -->
	<div
		bind:this={container}
		class="scrollbar-slim relative h-full w-full overflow-x-hidden overflow-y-auto [scrollbar-gutter:stable]"
	>
		<!-- The vertical padding EXISTS FOR THE FLOATING CHROME, so it follows the chrome rather than
		     being a constant. The pills sit over the first and last page — the find pill at `top-3`
		     (12px, icon-lg ≈ 40px) and the zoom toolbar at `bottom-4` — and without a gap they land on
		     the document's own letterhead, which is exactly the part of a page a reader looks at first.
		     4rem clears them with a little air.

		     Held at a constant `pt-16 pb-16` it was 8rem of dead space on a pane with NO pills: on the
		     template overview's 26rem thumbnail (`searchable={false} zoomable={false}`) that is nearly
		     a third of the panel spent reserving room for controls that never render, and it reads as
		     a broken gap above the page. With the chrome off it falls back to the uniform 12px.

		     Caveat worth keeping in view: the top-left pill is the HOST's Back button, not ours, so a
		     surface that floats its own chrome must not turn `searchable` off — today the only mount
		     that does is the thumbnail, which has no Back pill.

		     Horizontal padding stays 12px either way: HOST_PADDING measures it for the fit width, so
		     keep those two in step. -->
		<div
			bind:this={host}
			class="min-h-full w-full px-3 {searchable ? 'pt-16' : 'pt-3'} {zoomable ? 'pb-16' : 'pb-3'}"
		></div>
	</div>

	{#if searchable && findOpen}
		<!-- Floats over the pages rather than taking a toolbar row: it is a transient tool, and a
		     permanent bar would cost every preview pane a strip of height it doesn't need. -->
		<div
			class="bg-popover ring-foreground/10 absolute top-3 right-4 z-20 flex items-center gap-1 rounded-full py-1 pr-1 pl-3 shadow-md ring-1"
		>
			<SearchIcon class="text-muted-foreground size-3.5 shrink-0" />
			<!-- svelte-ignore a11y_autofocus -->
			<input
				bind:this={findInput}
				bind:value={query}
				oninput={() => runSearch()}
				onkeydown={(e) => {
					if (e.key === 'Enter') {
						e.preventDefault();
						void goToMatch(current + (e.shiftKey ? -1 : 1));
					}
				}}
				autofocus
				placeholder="Find in document"
				aria-label="Find in document"
				class="placeholder:text-muted-foreground w-40 bg-transparent text-sm outline-none"
			/>
			<span class="text-muted-foreground w-16 shrink-0 text-right text-xs tabular-nums">
				{#if indexing && !pageText.length}
					reading…
				{:else if !query}
					&nbsp;
				{:else if matches.length}
					{current + 1} / {matches.length}{truncated ? '+' : ''}
				{:else}
					no matches
				{/if}
			</span>
			<button
				type="button"
				onclick={() => goToMatch(current - 1)}
				disabled={!matches.length}
				aria-label="Previous match"
				class="hover:bg-muted rounded-full p-1 disabled:opacity-40"
			>
				<ChevronUpIcon class="size-3.5" />
			</button>
			<button
				type="button"
				onclick={() => goToMatch(current + 1)}
				disabled={!matches.length}
				aria-label="Next match"
				class="hover:bg-muted rounded-full p-1 disabled:opacity-40"
			>
				<ChevronDownIcon class="size-3.5" />
			</button>
			<button
				type="button"
				onclick={closeFind}
				aria-label="Close find"
				class="hover:bg-muted rounded-full p-1"
			>
				<XIcon class="size-3.5" />
			</button>
		</div>
	{:else if searchable && status === 'ready'}
		<button
			type="button"
			onclick={openFind}
			aria-label="Find in document"
			title="Find in document (⌘F)"
			class="bg-popover text-muted-foreground hover:text-foreground ring-foreground/10 absolute top-3 right-4 z-20 rounded-full p-2 shadow-sm ring-1"
		>
			<SearchIcon class="size-3.5" />
		</button>
	{/if}

	{#if truncated && findOpen}
		<p
			class="bg-popover text-muted-foreground ring-foreground/10 absolute top-14 right-4 z-20 rounded-md px-2.5 py-1 text-xs shadow-sm ring-1"
		>
			Showing the first {matches.length} matches.
		</p>
	{/if}

	{#if zoomable && status === 'ready'}
		<!-- BOTTOM-right, and that is a constraint rather than a preference: the top-left is the host's
		     Back pill on every surface that mounts this, and the top-right is the find bar here and a
		     mobile "Details" trigger on the client portal. The bottom edge is the one corner no caller
		     has claimed. Only shown once there is a document to zoom. -->
		<div
			class="bg-popover ring-foreground/10 absolute right-4 bottom-4 z-20 flex items-center gap-0.5 rounded-full p-1 shadow-md ring-1"
		>
			<Button
				variant="ghost"
				size="icon-sm"
				class="rounded-full"
				onclick={() => setZoom(stepZoom(zoom, -1))}
				disabled={zoom <= MIN_ZOOM}
				aria-label="Zoom out"
				title="Zoom out (⌘−)"
			>
				<MinusIcon class="size-3.5" />
			</Button>
			<!-- The readout IS the reset, the way a zoom control usually is: 100% means the page fits the
			     pane, so "fit the width" and "reset" are one act and a second control offering an
			     absolute 100% would render an A4 page NARROWER than the fit (see pdf-zoom.ts). -->
			<Button
				variant="ghost"
				size="sm"
				class="min-w-14 rounded-full px-2 tabular-nums"
				onclick={() => setZoom(1)}
				aria-label={`Zoom ${zoomLabel(zoom)}. Reset to fit the width`}
				title="Fit the width (⌘0)"
			>
				{zoomLabel(zoom)}
			</Button>
			<Button
				variant="ghost"
				size="icon-sm"
				class="rounded-full"
				onclick={() => setZoom(stepZoom(zoom, 1))}
				disabled={zoom >= MAX_ZOOM}
				aria-label="Zoom in"
				title="Zoom in (⌘+)"
			>
				<PlusIcon class="size-3.5" />
			</Button>
			{#if onexpand}
				<Separator orientation="vertical" class="mx-0.5 data-[orientation=vertical]:h-4" />
				<Button
					variant="ghost"
					size="icon-sm"
					class="rounded-full"
					onclick={() => onexpand(!expanded)}
					aria-pressed={expanded}
					aria-label={expanded ? 'Exit full page' : 'Expand to the full page'}
					title={expanded ? 'Exit full page (Esc)' : 'Expand to the full page'}
				>
					{#if expanded}
						<Minimize2Icon class="size-3.5" />
					{:else}
						<Maximize2Icon class="size-3.5" />
					{/if}
				</Button>
			{/if}
		</div>
	{/if}

	{#if status === 'error'}
		<div
			class="text-muted-foreground pointer-events-none absolute inset-0 flex items-center justify-center text-sm"
		>
			Couldn’t render the preview.
		</div>
	{:else if status === 'loading'}
		<PreviewSpinner />
	{/if}
</div>

<style>
	/* The minimum subset of pdf.js's own `web/pdf_viewer.css` that makes a text layer line up: it
	   positions each span absolutely and sizes it from CSS variables the layer container carries
	   (`--total-scale-factor` is set in `addTextLayer`). Vendored rather than imported because the
	   vendor sheet is ~1200 lines of viewer chrome we render none of — and because it would then be
	   a second thing to keep in step with the package version, instead of these twenty lines that
	   fail visibly if they ever drift.

	   `:global` because the layer's DOM is built by pdf.js, so Svelte's scoping class never reaches
	   it. Text is transparent and sits over the canvas: the canvas is what you SEE, this is what you
	   select, search and copy. */
	:global(.pdf-text-layer) {
		position: absolute;
		inset: 0;
		overflow: clip;
		text-align: initial;
		line-height: 1;
		letter-spacing: normal;
		word-spacing: normal;
		text-size-adjust: none;
		forced-color-adjust: none;
		transform-origin: 0 0;
		z-index: 1;
		--min-font-size: 1;
		--text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
		--min-font-size-inv: calc(1 / var(--min-font-size));
	}
	:global(.pdf-text-layer :is(span, br)) {
		color: transparent;
		position: absolute;
		white-space: pre;
		cursor: text;
		transform-origin: 0% 0%;
		user-select: text;
	}
	:global(.pdf-text-layer > :not(.markedContent)),
	:global(.pdf-text-layer .markedContent span:not(.markedContent)) {
		z-index: 1;
		--font-height: 0;
		--scale-x: 1;
		--rotate: 0deg;
		font-size: calc(var(--text-scale-factor) * var(--font-height));
		transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
	}
	:global(.pdf-text-layer .markedContent) {
		display: contents;
	}

	/* Find highlights, drawn by the CSS Custom Highlight API — no DOM surgery, so pdf.js's spans are
	   left exactly as it built them and a highlight can span several of them. Every hit gets the
	   quiet treatment; the one you are on gets the loud one. */
	:global(::highlight(pdf-find)) {
		background-color: color-mix(in oklch, var(--warning) 45%, transparent);
	}
	:global(::highlight(pdf-find-current)) {
		background-color: color-mix(in oklch, var(--warning) 85%, transparent);
	}
</style>
