/**
 * The touch-target audit — a tripwire for a control a thumb cannot reliably hit. The rule is
 * the platform one (Apple HIG, WCAG 2.5.5): every interactive element's HIT BOX is at least
 * 44×44 CSS px. The hit box is not the visible box — a 24px "View all ›" link is the right size
 * to look at — it is the border box plus any invisible extension the control carries: the
 * `tap-target` utility's `::before`, the switch and checkbox's `::after`, the period pills'
 * own `::before`. Geometry only: the browser has laid the page out, so we read the element's
 * rect and the computed insets of its absolutely positioned pseudo-elements.
 *
 * Two findings:
 *
 * - **small** — the hit box is under 44 on either axis. The usual fix is `tap-target` (an
 *   invisible extension), or `min-h-11` where the whole row is the control, or — for a field,
 *   which has no pseudo-elements — a real `min-height`.
 * - **overlap** — one control's EXTENSION reaches into another control's VISIBLE box. Two 36px
 *   icons at `gap-1` each extend 4px into the other, and the later one in the DOM wins the
 *   shared strip, so a tap on the edge of the first fires the second. Space them out, or make
 *   them big enough not to need extending. Nested controls (a link holding a button) are not
 *   compared; neither are two extensions merely touching each other.
 *
 * What is not checked: anything not rendered, disabled, `aria-hidden`, or tabindex −1 (a
 * collapsed row's hidden pills); screen-reader-only boxes; a link INLINE in a run of prose (a
 * word in a sentence is sized by the sentence — the WCAG inline exception); and anything
 * carrying `data-tap-ok`. A control that is the `control` of a wrapping `<label>` is credited
 * the label's box too, because the label activates it.
 */

export type TouchKind = 'small' | 'overlap';

export interface TouchFinding {
	kind: TouchKind;
	/** The control. For `overlap`, the one whose extension reaches into `other`. */
	element: HTMLElement;
	/** The hit box's size, in CSS px (for `small`). */
	width: number;
	height: number;
	/** The control reached into (for `overlap`). */
	other?: HTMLElement;
	/** A short, human path to the element (tag, block id, first classes), for the console. */
	path: string;
}

export interface TouchAuditOptions {
	/** The minimum, in CSS px. Default 44. */
	min?: number;
	/** Sub-pixel slack. Default 1. */
	tolerance?: number;
}

const ATTR = 'data-tap';
const OK = 'data-tap-ok';

/** What a thumb is expected to land on. */
export const INTERACTIVE_SELECTOR = [
	'a[href]',
	'button',
	'input:not([type=hidden])',
	'select',
	'textarea',
	'summary',
	'[role=button]',
	'[role=link]',
	'[role=tab]',
	'[role=menuitem]',
	'[role=menuitemcheckbox]',
	'[role=menuitemradio]',
	'[role=checkbox]',
	'[role=radio]',
	'[role=switch]',
	'[role=option]',
	'[role=slider]',
	'[tabindex]:not([tabindex="-1"])'
].join(',');

type Box = { top: number; right: number; bottom: number; left: number };

/**
 * A used inset, in px. `getComputedStyle` usually hands back the used length (`-4px`); some
 * engines hand back the specified `min(0px, calc(50% - 22px))` that `tap-target` is written in.
 * `parseFloat` of that is NaN, which used to read as 0 — the extension vanished, the control
 * measured under 44, and the dev mark drew a dashed ring around a button that was fine.
 * `percentOf` is the padding-box size the percentage resolves against (width for left/right,
 * height for top/bottom).
 */
const pxBorder = (v: string): number => {
	const n = parseFloat(v);
	return Number.isFinite(n) ? n : 0;
};

function insetLength(value: string, percentOf: number): number {
	const v = value.trim();
	if (v === 'auto' || v === '') return 0;
	const plain = /^(-?[\d.]+)px$/.exec(v);
	if (plain) return parseFloat(plain[1]!);
	const min =
		/^min\(\s*(-?[\d.]+)px\s*,\s*calc\(\s*(-?[\d.]+)%\s*([+-])\s*([\d.]+)px\s*\)\s*\)$/.exec(v);
	if (min) {
		const fixed = parseFloat(min[1]!);
		const pct = (parseFloat(min[2]!) / 100) * percentOf;
		const offset = parseFloat(min[4]!);
		const calc = min[3] === '+' ? pct + offset : pct - offset;
		return Math.min(fixed, calc);
	}
	const n = parseFloat(v);
	return Number.isFinite(n) ? n : 0;
}

function rendered(cs: CSSStyleDeclaration): boolean {
	return cs.display !== 'none' && cs.display !== 'contents' && cs.visibility !== 'hidden';
}

function inert(el: HTMLElement): boolean {
	if (el.getAttribute('aria-hidden') === 'true') return true;
	if (el.getAttribute('tabindex') === '-1') return true;
	if (el.getAttribute('aria-disabled') === 'true') return true;
	if ('disabled' in el && (el as HTMLButtonElement).disabled) return true;
	return false;
}

/** `data-tap-ok` on the element or any ancestor. */
function waived(el: HTMLElement): boolean {
	return el.closest(`[${OK}]`) !== null;
}

/** A link that is a word in a sentence: inline, with text beside it in the same parent. */
function inlineInProse(el: HTMLElement, cs: CSSStyleDeclaration): boolean {
	if (cs.display !== 'inline' || !el.parentElement) return false;
	for (const node of el.parentElement.childNodes) {
		if (node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim() !== '') return true;
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

const union = (a: Box, b: Box): Box => ({
	top: Math.min(a.top, b.top),
	right: Math.max(a.right, b.right),
	bottom: Math.max(a.bottom, b.bottom),
	left: Math.min(a.left, b.left)
});

const fromRect = (r: DOMRect): Box => ({
	top: r.top,
	right: r.right,
	bottom: r.bottom,
	left: r.left
});

/**
 * The element's hit box: its border box grown by every absolutely positioned pseudo-element
 * with content. An absolute pseudo's insets are measured from the element's PADDING box, so a
 * bordered button's `top: -5px` reaches 4px past its border edge, not 5.
 */
export function hitBox(el: HTMLElement): Box {
	const r = el.getBoundingClientRect();
	const cs = getComputedStyle(el);
	let box = fromRect(r);
	// Insets are measured from the padding box, so the percentage resolves against it.
	const padWidth = r.width - pxBorder(cs.borderLeftWidth) - pxBorder(cs.borderRightWidth);
	const padHeight = r.height - pxBorder(cs.borderTopWidth) - pxBorder(cs.borderBottomWidth);
	for (const pseudo of ['::before', '::after'] as const) {
		const ps = getComputedStyle(el, pseudo);
		// `content: ''` generates a box (that is the whole of `tap-target`). Engines serialize it
		// as `""` or as an empty string; only `none`/`normal` mean there is no box. Skipping the
		// empty string outlined every extended control.
		if (ps.content === 'none' || ps.content === 'normal' || ps.position !== 'absolute') continue;
		box = union(box, {
			top: r.top + pxBorder(cs.borderTopWidth) + insetLength(ps.top, padHeight),
			left: r.left + pxBorder(cs.borderLeftWidth) + insetLength(ps.left, padWidth),
			right: r.right - pxBorder(cs.borderRightWidth) - insetLength(ps.right, padWidth),
			bottom: r.bottom - pxBorder(cs.borderBottomWidth) - insetLength(ps.bottom, padHeight)
		});
	}
	return box;
}

/** Whether two boxes share area (not merely an edge). */
function intersects(a: Box, b: Box, tol: number): boolean {
	return (
		a.left < b.right - tol &&
		b.left < a.right - tol &&
		a.top < b.bottom - tol &&
		b.top < a.bottom - tol
	);
}

/**
 * Walk `root` and report every control whose hit box is under the minimum, and every
 * extension that reaches into another control. Pure: marks nothing, logs nothing.
 */
export function auditTouchTargets(
	root: ParentNode,
	options: TouchAuditOptions = {}
): TouchFinding[] {
	const min = options.min ?? 44;
	const tol = options.tolerance ?? 1;
	const out: TouchFinding[] = [];
	const seen: { el: HTMLElement; hit: Box; visible: Box }[] = [];

	for (const el of root.querySelectorAll<HTMLElement>(INTERACTIVE_SELECTOR)) {
		const cs = getComputedStyle(el);
		if (!rendered(cs) || inert(el) || waived(el)) continue;
		const r = el.getBoundingClientRect();
		if (r.width <= 1 || r.height <= 1) continue;
		if (inlineInProse(el, cs)) continue;

		let hit = hitBox(el);
		// A label's control is reached through the label too.
		const label = el.closest('label');
		if (label && label.control === el) hit = union(hit, fromRect(label.getBoundingClientRect()));

		const visible = fromRect(r);
		seen.push({ el, hit, visible });

		const width = hit.right - hit.left;
		const height = hit.bottom - hit.top;
		if (width + tol < min || height + tol < min) {
			out.push({ kind: 'small', element: el, width, height, path: describe(el) });
		}
	}

	// Extensions reaching into neighbours. Only an EXTENSION can trespass: two visible boxes
	// cannot overlap unless one is on top of the other on purpose.
	for (const a of seen) {
		const extended =
			a.hit.top < a.visible.top ||
			a.hit.left < a.visible.left ||
			a.hit.right > a.visible.right ||
			a.hit.bottom > a.visible.bottom;
		if (!extended) continue;
		for (const b of seen) {
			if (a === b || a.el.contains(b.el) || b.el.contains(a.el)) continue;
			if (intersects(a.hit, b.visible, tol) && !intersects(a.visible, b.visible, tol)) {
				out.push({
					kind: 'overlap',
					element: a.el,
					other: b.el,
					width: a.hit.right - a.hit.left,
					height: a.hit.bottom - a.hit.top,
					path: describe(a.el)
				});
			}
		}
	}
	return out;
}

/** Outline the offenders (`data-tap="small|overlap"`), clearing the last run's marks first. */
export function markTouchTargets(root: ParentNode, findings: TouchFinding[]): void {
	for (const el of root.querySelectorAll(`[${ATTR}]`)) el.removeAttribute(ATTR);
	for (const f of findings) f.element.setAttribute(ATTR, f.kind);
}

/** One console group per run: a line per finding, with the element attached so it is clickable. */
export function reportTouchTargets(findings: TouchFinding[], where = location.pathname): void {
	if (findings.length === 0) return;
	const n = findings.length;
	console.groupCollapsed(`[touch] ${n} finding${n === 1 ? '' : 's'} on ${where}`);
	for (const f of findings) {
		const size = `${Math.round(f.width)}×${Math.round(f.height)}`;
		if (f.kind === 'overlap') {
			console.warn(`overlap: hit area reaches a neighbour — ${f.path}`, f.element, f.other);
		} else {
			console.warn(`small: ${size} — ${f.path}`, f.element);
		}
	}
	console.groupEnd();
}
