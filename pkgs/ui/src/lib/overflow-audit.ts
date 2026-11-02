/**
 * The overflow audit — a tripwire for content that spills out of its box or is clipped behind
 * one. Nothing in the layout rules can promise "never": a 12-column grid cut five ways gives a
 * tile 165px, and "Correspondence" beside its mark needs 166; a filter chip's label wraps in one
 * language and not another; a flex child without `min-w-0` grows past its row the first time a
 * fund name is long. The portal runs this in development after every navigation and layout
 * change and shouts — console, and a dashed outline on the element — so the case is seen on the
 * screen it happens on rather than found in a client's screenshot. Geometry only, no CSS
 * heuristics: the browser has already laid the page out, so we ask it where the boxes landed.
 *
 * Two findings:
 *
 * - **spill** — a child's border box, or a run of text, extends past its parent's padding box
 *   while the parent's overflow is `visible`. The content renders, over whatever is next to it.
 *   A NEGATIVE MARGIN is read as a declared bleed (`-mr-1.5` on a "View all" link, `-mx-2` on the
 *   phone grid, the deck's `-mt-[3.25rem]` overlap) and that much excursion is allowed.
 *   Positioned children (`absolute`/`fixed`) escape their parent by design and are not spills.
 * - **clip** — an ancestor with overflow `hidden`/`clip` cuts off CONTENT: an element with its
 *   own text, an image, or a control. Decoration is not content: anything `aria-hidden`,
 *   `pointer-events-none` or `visibility:hidden` under the clipper is ignored (sheens, bleeding
 *   sparklines, a collapsed period row's hidden pills). `text-overflow: ellipsis` and
 *   `-webkit-line-clamp` are intentional clips and skip the check; so is any element carrying
 *   `data-overflow-ok`, for the rare bleed the rules above cannot read. Scroll containers are
 *   not clippers — their content is theirs to scroll — and the walk does not descend into them.
 *
 * `tolerance` absorbs sub-pixel rounding (1px). Screen-reader-only boxes (1×1) are skipped.
 */

import { watchLayout } from './layout-watch.js';

export type OverflowKind = 'spill' | 'clip';

export interface OverflowFinding {
	kind: OverflowKind;
	/** The box that was spilled out of, or that did the clipping. */
	container: HTMLElement;
	/** What spilled or was clipped — an element, or the element whose text did. */
	content: HTMLElement;
	/** The excursion past the container's padding box, in CSS px, by edge. */
	by: { top: number; right: number; bottom: number; left: number };
	/** A short, human path to the container (tag, block id, first classes), for the console. */
	path: string;
}

export interface AuditOptions {
	/** Sub-pixel slack. Default 1. */
	tolerance?: number;
}

const ATTR = 'data-overflow';
const OK = 'data-overflow-ok';

type Box = { top: number; right: number; bottom: number; left: number };

const px = (v: string): number => {
	const n = parseFloat(v);
	return Number.isFinite(n) ? n : 0;
};

const scrolls = (v: string) => v === 'auto' || v === 'scroll';
const clips = (v: string) => v === 'hidden' || v === 'clip';

/** The padding box: the border box inset by the borders. */
function paddingBox(el: HTMLElement, cs: CSSStyleDeclaration): Box {
	const r = el.getBoundingClientRect();
	return {
		top: r.top + px(cs.borderTopWidth),
		right: r.right - px(cs.borderRightWidth),
		bottom: r.bottom - px(cs.borderBottomWidth),
		left: r.left + px(cs.borderLeftWidth)
	};
}

/** How far `inner` lies outside `outer`, per edge; zero where it does not. */
function excursion(inner: Box, outer: Box, tol: number): Box | null {
	const by = {
		top: outer.top - inner.top,
		right: inner.right - outer.right,
		bottom: inner.bottom - outer.bottom,
		left: outer.left - inner.left
	};
	const any = Object.values(by).some((v) => v > tol);
	if (!any) return null;
	return {
		top: Math.max(0, by.top),
		right: Math.max(0, by.right),
		bottom: Math.max(0, by.bottom),
		left: Math.max(0, by.left)
	};
}

const isSrOnly = (r: DOMRect) => r.width <= 1 || r.height <= 1;
const hasBox = (r: DOMRect) => r.width > 0 && r.height > 0;

function rendered(el: HTMLElement, cs: CSSStyleDeclaration): boolean {
	return cs.display !== 'none' && cs.display !== 'contents' && cs.visibility !== 'hidden';
}

/** Elements and text whose loss a reader would notice. */
function isContent(el: HTMLElement, cs: CSSStyleDeclaration): boolean {
	if (el.getAttribute('aria-hidden') === 'true') return false;
	if (cs.pointerEvents === 'none') return false;
	if (/^(A|BUTTON|INPUT|SELECT|TEXTAREA|IMG)$/.test(el.tagName)) return true;
	for (const node of el.childNodes) {
		if (node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim() !== '') return true;
	}
	return false;
}

/** `data-overflow-ok` on the element or any ancestor up to (not including) `stop`. */
function waived(el: HTMLElement, stop: HTMLElement | null): boolean {
	let e: HTMLElement | null = el;
	while (e && e !== stop) {
		if (e.hasAttribute(OK)) return true;
		e = e.parentElement;
	}
	return false;
}

function describe(el: HTMLElement): string {
	const bits: string[] = [];
	let e: HTMLElement | null = el;
	while (e && e !== document.body && bits.length < 4) {
		let s = e.tagName.toLowerCase();
		if (e.dataset['blockId']) s += `[block=${e.dataset['blockId']}]`;
		else if (e.id) s += `#${e.id}`;
		else {
			const cls = typeof e.className === 'string' ? e.className.split(/\s+/).filter(Boolean) : [];
			if (cls.length) s += '.' + cls.slice(0, 2).join('.');
		}
		bits.unshift(s);
		e = e.parentElement;
	}
	return bits.join(' > ');
}

/** The union of the rendered rects of an element's own text nodes, or null when it has none. */
function textBox(el: HTMLElement): Box | null {
	let box: Box | null = null;
	for (const node of el.childNodes) {
		if (node.nodeType !== Node.TEXT_NODE || (node.textContent ?? '').trim() === '') continue;
		const range = document.createRange();
		range.selectNodeContents(node);
		const r = range.getBoundingClientRect();
		if (!hasBox(r)) continue;
		box = box
			? {
					top: Math.min(box.top, r.top),
					right: Math.max(box.right, r.right),
					bottom: Math.max(box.bottom, r.bottom),
					left: Math.min(box.left, r.left)
				}
			: { top: r.top, right: r.right, bottom: r.bottom, left: r.left };
	}
	return box;
}

/**
 * Walk `root` and report every spill and clip. Pure: marks nothing, logs nothing.
 */
export function auditOverflow(root: ParentNode, options: AuditOptions = {}): OverflowFinding[] {
	const tol = options.tolerance ?? 1;
	const out: OverflowFinding[] = [];
	const styles = new WeakMap<HTMLElement, CSSStyleDeclaration>();
	const styleOf = (el: HTMLElement) => {
		let cs = styles.get(el);
		if (!cs) {
			cs = getComputedStyle(el);
			styles.set(el, cs);
		}
		return cs;
	};

	for (const el of root.querySelectorAll<HTMLElement>('*')) {
		const cs = styleOf(el);
		if (!rendered(el, cs)) continue;
		const rect = el.getBoundingClientRect();
		if (!hasBox(rect) || isSrOnly(rect)) continue;
		if (el.hasAttribute(OK)) continue;
		const box = paddingBox(el, cs);
		const ox = cs.overflowX || cs.overflow;
		const oy = cs.overflowY || cs.overflow;
		// One non-visible axis makes the box a scroll/clip container on both (CSS computes the
		// other to `auto`), so a spill is only possible when both are visible.
		const contains = clips(ox) || scrolls(ox) || clips(oy) || scrolls(oy);

		// ── spill: the children and the text of a box that does not contain them ──
		if (!contains) {
			for (const child of el.children) {
				if (!(child instanceof HTMLElement)) continue;
				const ccs = styleOf(child);
				if (!rendered(child, ccs)) continue;
				if (ccs.position === 'absolute' || ccs.position === 'fixed') continue;
				if (child.hasAttribute(OK)) continue;
				const cr = child.getBoundingClientRect();
				if (!hasBox(cr) || isSrOnly(cr)) continue;
				// A negative margin is a declared bleed: allow that much.
				const inner: Box = {
					top: cr.top - Math.min(0, px(ccs.marginTop)),
					right: cr.right + Math.min(0, px(ccs.marginRight)),
					bottom: cr.bottom + Math.min(0, px(ccs.marginBottom)),
					left: cr.left - Math.min(0, px(ccs.marginLeft))
				};
				// Both axes for a box: a child taller than a fixed-height parent is as real a spill
				// as one wider than it (a `h-10` row holding three lines). An INLINE child is judged
				// horizontally only, like text — its line box standing taller than a `leading-none`
				// parent is typography, not a layout fault.
				if (ccs.display === 'inline') {
					inner.top = box.top;
					inner.bottom = box.bottom;
				}
				const by = excursion(inner, box, tol);
				if (by) out.push({ kind: 'spill', container: el, content: child, by, path: describe(el) });
			}
			// Text is judged horizontally only: a glyph's ink overhanging a tight line-height is
			// typography, not a layout fault. And the range must agree with scrollWidth: under
			// `white-space: pre-wrap` a trailing space HANGS past the box (the range sees it, the
			// browser does not count it as overflow), while a `::before` hit-area counts toward
			// scrollWidth and is nothing a reader can see. Only what both report is a spill.
			const text = el.clientWidth > 0 && el.scrollWidth - el.clientWidth > tol ? textBox(el) : null;
			if (text) {
				const by = excursion({ ...text, top: box.top, bottom: box.bottom }, box, tol);
				if (by) out.push({ kind: 'spill', container: el, content: el, by, path: describe(el) });
			}
		}

		// ── clip: the content under a box that cuts ──
		if ((clips(ox) || clips(oy)) && cs.textOverflow !== 'ellipsis' && !lineClamped(cs)) {
			const clipBox: Box = {
				top: clips(oy) ? box.top : -Infinity,
				right: clips(ox) ? box.right : Infinity,
				bottom: clips(oy) ? box.bottom : Infinity,
				left: clips(ox) ? box.left : -Infinity
			};
			const stack: HTMLElement[] = [...el.children].filter(
				(c): c is HTMLElement => c instanceof HTMLElement
			);
			while (stack.length) {
				const d = stack.pop()!;
				const dcs = styleOf(d);
				if (!rendered(d, dcs) || waived(d, el)) continue;
				// A nested scroller owns what is inside it.
				if (scrolls(dcs.overflowX || dcs.overflow) || scrolls(dcs.overflowY || dcs.overflow)) {
					continue;
				}
				// Decoration is not content, and nothing under it is either.
				if (d.getAttribute('aria-hidden') === 'true' || dcs.pointerEvents === 'none') continue;
				for (const c of d.children) if (c instanceof HTMLElement) stack.push(c);
				if (!isContent(d, dcs)) continue;
				const dr = d.getBoundingClientRect();
				if (!hasBox(dr) || isSrOnly(dr)) continue;
				const by = excursion(dr, clipBox, tol);
				if (by) out.push({ kind: 'clip', container: el, content: d, by, path: describe(el) });
			}
		}
	}
	return out;
}

function lineClamped(cs: CSSStyleDeclaration): boolean {
	const v = cs.getPropertyValue('-webkit-line-clamp');
	return v !== '' && v !== 'none';
}

/** Outline the offenders (`data-overflow="spill|clip"`), clearing the last run's marks first. */
export function markOverflow(root: ParentNode, findings: OverflowFinding[]): void {
	for (const el of root.querySelectorAll(`[${ATTR}]`)) el.removeAttribute(ATTR);
	for (const f of findings) f.content.setAttribute(ATTR, f.kind);
}

/** One console group per run: a line per finding, with the element attached so it is clickable. */
export function reportOverflow(findings: OverflowFinding[], where = location.pathname): void {
	if (findings.length === 0) return;
	const n = findings.length;
	console.groupCollapsed(`[overflow] ${n} finding${n === 1 ? '' : 's'} on ${where}`);
	for (const f of findings) {
		const edges = (Object.entries(f.by) as [string, number][])
			.filter(([, v]) => v > 0)
			.map(([k, v]) => `${k} ${Math.round(v)}px`)
			.join(', ');
		console.warn(`${f.kind}: ${edges} — ${f.path}`, f.content, f.container);
	}
	console.groupEnd();
}

export interface WatchOptions extends AuditOptions {
	/** Called with every run's findings (after marking). Default: `reportOverflow`. */
	onfindings?: (findings: OverflowFinding[]) => void;
	/** Settle time after the last change before auditing. Default 250ms. */
	debounceMs?: number;
}

/**
 * Audit `root` now and again whenever it changes size or content, debounced. Returns a stop
 * function. Development only — the walk is O(elements) and runs getComputedStyle on each.
 * (`watchLayout` is the shared scheduler; use it directly to run this and the touch audit off
 * one observer set, as the client portal does.)
 */
export function watchOverflow(root: HTMLElement, options: WatchOptions = {}): () => void {
	const report = options.onfindings ?? ((f: OverflowFinding[]) => reportOverflow(f));
	return watchLayout(root, {
		debounceMs: options.debounceMs,
		run(el) {
			const findings = auditOverflow(el, options);
			markOverflow(el, findings);
			report(findings);
		},
		cleanup(el) {
			markOverflow(el, []);
		}
	});
}
