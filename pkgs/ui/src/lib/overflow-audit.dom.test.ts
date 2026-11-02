import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { auditOverflow, markOverflow, reportOverflow, watchOverflow } from './overflow-audit';

/**
 * jsdom lays nothing out, so every box here is declared: `rect(el, {...})` sets what
 * getBoundingClientRect returns for an element (and for a Range over its text, when `text` is
 * given). Styles come from inline `style=""` — jsdom's getComputedStyle reflects those, which is
 * all the audit reads (overflow, position, margins, borders, display, text-overflow).
 */

type R = { left: number; top: number; right: number; bottom: number };
const rects = new WeakMap<Node, R>();
const textRects = new WeakMap<Node, R>();

const toDOMRect = (r: R): DOMRect =>
	({
		x: r.left,
		y: r.top,
		left: r.left,
		top: r.top,
		right: r.right,
		bottom: r.bottom,
		width: r.right - r.left,
		height: r.bottom - r.top,
		toJSON: () => r
	}) as DOMRect;

const ZERO: R = { left: 0, top: 0, right: 0, bottom: 0 };

function rect(el: Element, r: R, text?: R) {
	rects.set(el, r);
	if (text)
		for (const n of el.childNodes) if (n.nodeType === Node.TEXT_NODE) textRects.set(n, text);
}

/** Build a tree from HTML; return the root so boxes can be declared on it. */
function tree(html: string): HTMLElement {
	const root = document.createElement('div');
	root.innerHTML = html;
	document.body.append(root);
	rect(root, { left: 0, top: 0, right: 1000, bottom: 1000 });
	return root;
}

const q = (root: ParentNode, sel: string) => root.querySelector<HTMLElement>(sel)!;

beforeEach(() => {
	vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
		return toDOMRect(rects.get(this) ?? ZERO);
	});
	// jsdom's Range has no getBoundingClientRect at all, so it is defined rather than spied.
	Object.defineProperty(Range.prototype, 'getBoundingClientRect', {
		configurable: true,
		value(this: Range) {
			return toDOMRect(textRects.get(this.startContainer) ?? ZERO);
		}
	});
	// jsdom reports scrollWidth/clientWidth as 0; the text check wants them to agree with the
	// declared rects, so derive them: client = own box, scroll = own box or the text box.
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get(this: HTMLElement) {
			const r = rects.get(this);
			return r ? r.right - r.left : 0;
		}
	});
	Object.defineProperty(HTMLElement.prototype, 'scrollWidth', {
		configurable: true,
		get(this: HTMLElement) {
			const r = rects.get(this);
			if (!r) return 0;
			let right = r.right;
			for (const n of this.childNodes) {
				const t = textRects.get(n);
				if (t) right = Math.max(right, t.right);
			}
			return right - r.left;
		}
	});
});

afterEach(() => {
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

describe('auditOverflow — spill', () => {
	it('reports a child whose border box runs past its parent’s padding box', () => {
		const root = tree('<div id="p"><div id="c"></div></div>');
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#c'), { left: 0, top: 0, right: 230, bottom: 100 });
		const found = auditOverflow(root);
		expect(found).toHaveLength(1);
		expect(found[0]).toMatchObject({
			kind: 'spill',
			by: { right: 30, left: 0, top: 0, bottom: 0 }
		});
		expect(found[0]!.container.id).toBe('p');
		expect(found[0]!.content.id).toBe('c');
	});

	it('is quiet within the tolerance (sub-pixel rounding)', () => {
		const root = tree('<div id="p"><div id="c"></div></div>');
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#c'), { left: 0, top: 0, right: 200.9, bottom: 100 });
		expect(auditOverflow(root)).toEqual([]);
	});

	it('reads a negative margin as a declared bleed and allows that much', () => {
		const root = tree('<div id="p"><a id="c" style="margin-right:-6px">View all</a></div>');
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 40 });
		rect(q(root, '#c'), { left: 150, top: 0, right: 206, bottom: 40 });
		expect(auditOverflow(root)).toEqual([]);
		// …but not more than it.
		rect(q(root, '#c'), { left: 150, top: 0, right: 210, bottom: 40 });
		expect(auditOverflow(root)).toHaveLength(1);
	});

	it('ignores positioned children — they escape their parent by design', () => {
		const root = tree(
			'<div id="p"><div id="a" style="position:absolute"></div><div id="f" style="position:fixed"></div></div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#a'), { left: -40, top: -40, right: 400, bottom: 400 });
		rect(q(root, '#f'), { left: 0, top: 0, right: 2000, bottom: 2000 });
		expect(auditOverflow(root)).toEqual([]);
	});

	it('judges an inline child horizontally only — a tall line box over leading-none is typography', () => {
		const root = tree('<p id="p"><span id="s">£452,728.25</span></p>');
		rect(q(root, '#p'), { left: 0, top: 10, right: 300, bottom: 50 });
		rect(q(root, '#s'), { left: 0, top: 4, right: 300, bottom: 56 });
		expect(auditOverflow(root)).toEqual([]);
		rect(q(root, '#s'), { left: 0, top: 4, right: 330, bottom: 56 });
		expect(auditOverflow(root)).toHaveLength(1);
	});

	it('reports text running out of its block when scrollWidth agrees', () => {
		const root = tree('<span id="t">Correspondence</span>');
		rect(
			q(root, '#t'),
			{ left: 0, top: 0, right: 95, bottom: 20 },
			{ left: 0, top: 0, right: 118, bottom: 20 }
		);
		const found = auditOverflow(root);
		expect(found).toHaveLength(1);
		expect(found[0]).toMatchObject({ kind: 'spill', by: { right: 23 } });
		expect(found[0]!.content.id).toBe('t');
	});

	it('does not report hanging trailing whitespace (the range sees it; the browser does not)', () => {
		const root = tree('<p id="t" style="white-space:pre-wrap">Hi — yes, happy to. </p>');
		// Range extends 4px past the box; scrollWidth (derived from the text rect here) would
		// too, so pin the browser's view explicitly: no scrollable overflow.
		rect(
			q(root, '#t'),
			{ left: 0, top: 0, right: 294, bottom: 60 },
			{ left: 0, top: 0, right: 298, bottom: 60 }
		);
		Object.defineProperty(q(root, '#t'), 'scrollWidth', { value: 294 });
		expect(auditOverflow(root)).toEqual([]);
	});

	it('skips screen-reader-only boxes and unrendered elements', () => {
		const root = tree(
			'<div id="p"><span id="sr">Account</span><div id="h" style="display:none"></div></div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(
			q(root, '#sr'),
			{ left: 0, top: 0, right: 1, bottom: 1 },
			{ left: 0, top: 0, right: 60, bottom: 20 }
		);
		rect(q(root, '#h'), { left: 0, top: 0, right: 900, bottom: 900 });
		expect(auditOverflow(root)).toEqual([]);
	});

	it('honours data-overflow-ok on the container or the child', () => {
		const root = tree(
			'<div id="p" data-overflow-ok><div id="c"></div></div><div id="q"><div id="d" data-overflow-ok></div></div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#c'), { left: 0, top: 0, right: 300, bottom: 100 });
		rect(q(root, '#q'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#d'), { left: 0, top: 0, right: 300, bottom: 100 });
		expect(auditOverflow(root)).toEqual([]);
	});
});

describe('auditOverflow — clip', () => {
	it('reports content cut off by an overflow-hidden ancestor, deep in the tree', () => {
		const root = tree(
			'<div id="p" style="overflow-x:hidden;overflow-y:hidden"><div id="m"><button id="b">Send</button></div></div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#m'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#b'), { left: 150, top: 0, right: 260, bottom: 40 });
		const found = auditOverflow(root);
		// Both truths, in document order of the container: the ancestor clips the button, and
		// the button spills its own parent.
		expect(found.map((f) => [f.kind, f.container.id, f.content.id])).toEqual([
			['clip', 'p', 'b'],
			['spill', 'm', 'b']
		]);
		expect(found[0]).toMatchObject({ kind: 'clip', by: { right: 60 } });
	});

	it('clips on one axis only when only that axis hides', () => {
		const root = tree(
			'<div id="p" style="overflow-x:hidden;overflow-y:visible"><p id="t">Text</p></div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#t'), { left: 0, top: 0, right: 200, bottom: 300 });
		expect(auditOverflow(root)).toEqual([]);
		rect(q(root, '#t'), { left: 0, top: 0, right: 250, bottom: 300 });
		expect(auditOverflow(root)).toHaveLength(1);
	});

	it('does not count decoration: aria-hidden, pointer-events-none, or anything beneath them', () => {
		const root = tree(
			'<div id="p" style="overflow-x:hidden;overflow-y:hidden">' +
				'<div id="sheen" style="pointer-events:none"><p>bleed</p></div>' +
				'<div id="hid" aria-hidden="true"><button>3M</button></div>' +
				'</div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		for (const sel of ['#sheen', '#sheen p', '#hid', '#hid button'])
			rect(q(root, sel), { left: -50, top: -50, right: 400, bottom: 400 });
		expect(auditOverflow(root)).toEqual([]);
	});

	it('treats ellipsis and line-clamp as intentional clips', () => {
		const root = tree(
			'<div id="e" style="overflow-x:hidden;overflow-y:hidden;text-overflow:ellipsis;white-space:nowrap"><span id="es">A long name</span></div>' +
				'<div id="l" style="overflow-x:hidden;overflow-y:hidden;-webkit-line-clamp:2;display:-webkit-box"><span id="ls">Two lines of it</span></div>'
		);
		rect(q(root, '#e'), { left: 0, top: 0, right: 100, bottom: 20 });
		rect(q(root, '#es'), { left: 0, top: 0, right: 300, bottom: 20 });
		rect(q(root, '#l'), { left: 0, top: 0, right: 100, bottom: 40 });
		rect(q(root, '#ls'), { left: 0, top: 0, right: 100, bottom: 120 });
		expect(auditOverflow(root)).toEqual([]);
	});

	it('does not descend into a nested scroll container — its content is its own', () => {
		const root = tree(
			'<div id="p" style="overflow-x:hidden;overflow-y:hidden"><div id="s" style="overflow-y:auto"><p id="t">Row 40</p></div></div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#s'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#t'), { left: 0, top: 900, right: 200, bottom: 920 });
		expect(auditOverflow(root)).toEqual([]);
	});

	it('a scroll container is not a clipper and is not a spill either', () => {
		const root = tree('<div id="s" style="overflow-x:auto"><div id="wide"></div></div>');
		rect(q(root, '#s'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#wide'), { left: 0, top: 0, right: 900, bottom: 100 });
		expect(auditOverflow(root)).toEqual([]);
	});
});

describe('markOverflow / reportOverflow', () => {
	it('marks the content with the kind and clears the previous run’s marks', () => {
		const root = tree(
			'<div id="p"><div id="c"></div><div id="old" data-overflow="clip"></div></div>'
		);
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#c'), { left: 0, top: 0, right: 300, bottom: 100 });
		rect(q(root, '#old'), { left: 0, top: 0, right: 10, bottom: 10 });
		markOverflow(root, auditOverflow(root));
		expect(q(root, '#c').getAttribute('data-overflow')).toBe('spill');
		expect(q(root, '#old').hasAttribute('data-overflow')).toBe(false);
	});

	it('logs one collapsed group with a line per finding, and nothing for none', () => {
		const group = vi.spyOn(console, 'groupCollapsed').mockImplementation(() => {});
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		const end = vi.spyOn(console, 'groupEnd').mockImplementation(() => {});
		reportOverflow([], '/x');
		expect(group).not.toHaveBeenCalled();
		const root = tree('<div id="p"><div id="c"></div></div>');
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#c'), { left: 0, top: 0, right: 300, bottom: 140 });
		reportOverflow(auditOverflow(root), '/documents');
		expect(group).toHaveBeenCalledWith('[overflow] 1 finding on /documents');
		expect(warn).toHaveBeenCalledTimes(1);
		expect(warn.mock.calls[0]![0]).toMatch(/^spill: right 100px, bottom 40px — /);
		expect(end).toHaveBeenCalledTimes(1);
	});
});

describe('watchOverflow', () => {
	it('audits after the debounce, re-runs on mutation, and cleans its marks on stop', async () => {
		vi.useFakeTimers();
		const raf = vi
			.spyOn(window, 'requestAnimationFrame')
			.mockImplementation((cb) => (cb(0), 1) as unknown as number);
		const root = tree('<div id="p"><div id="c"></div></div>');
		rect(q(root, '#p'), { left: 0, top: 0, right: 200, bottom: 100 });
		rect(q(root, '#c'), { left: 0, top: 0, right: 300, bottom: 100 });
		const runs: number[] = [];
		const stop = watchOverflow(root, { onfindings: (f) => runs.push(f.length), debounceMs: 50 });
		expect(runs).toEqual([]);
		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toEqual([1]);
		expect(q(root, '#c').getAttribute('data-overflow')).toBe('spill');

		// A content change schedules another run; our own mark attribute does not.
		q(root, '#c').className = 'changed';
		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toEqual([1, 1]);

		stop();
		expect(q(root, '#c').hasAttribute('data-overflow')).toBe(false);
		q(root, '#c').className = 'again';
		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toEqual([1, 1]);
		raf.mockRestore();
		vi.useRealTimers();
	});
});
