import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { auditTouchTargets, hitBox, markTouchTargets, reportTouchTargets } from './touch-audit';

/**
 * jsdom lays nothing out and has no pseudo-elements, so both are declared: `rect(el, {...})`
 * sets what getBoundingClientRect returns, `pseudo(el, '::before', {...})` what
 * getComputedStyle(el, '::before') returns. Element styles come from inline `style=""`.
 */

type R = { left: number; top: number; right: number; bottom: number };
const rects = new WeakMap<Node, R>();
const pseudos = new WeakMap<Element, Record<string, Record<string, string>>>();

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

function rect(el: Element, r: R) {
	rects.set(el, r);
}

function pseudo(el: Element, which: '::before' | '::after', style: Record<string, string>) {
	const cur = pseudos.get(el) ?? {};
	cur[which] = style;
	pseudos.set(el, cur);
}

/** An invisible extension, the way `tap-target` declares one: insets off the border box. */
const extend = (el: Element, by: number, which: '::before' | '::after' = '::before') =>
	pseudo(el, which, {
		content: '""',
		position: 'absolute',
		top: `${-by}px`,
		right: `${-by}px`,
		bottom: `${-by}px`,
		left: `${-by}px`
	});

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
	const real = window.getComputedStyle.bind(window);
	vi.spyOn(window, 'getComputedStyle').mockImplementation(
		(el: Element, pseudoElt?: string | null) => {
			if (!pseudoElt) return real(el);
			const declared = pseudos.get(el)?.[pseudoElt];
			// No pseudo declared: the browser's answer is `content: none`.
			return {
				content: 'none',
				position: 'static',
				top: '',
				right: '',
				bottom: '',
				left: '',
				...declared
			} as CSSStyleDeclaration;
		}
	);
});

afterEach(() => {
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

describe('hitBox', () => {
	it('is the border box when nothing extends it', () => {
		const root = tree('<button id="b">Go</button>');
		rect(q(root, '#b'), { left: 10, top: 10, right: 110, bottom: 38 });
		expect(hitBox(q(root, '#b'))).toEqual({ left: 10, top: 10, right: 110, bottom: 38 });
	});

	it('grows by an absolutely positioned pseudo-element’s negative insets', () => {
		const root = tree('<a id="a" href="/x">View all</a>');
		rect(q(root, '#a'), { left: 100, top: 100, right: 172, bottom: 124 });
		pseudo(q(root, '#a'), '::before', {
			content: '""',
			position: 'absolute',
			top: '-10px',
			bottom: '-10px',
			left: '0px',
			right: '0px'
		});
		expect(hitBox(q(root, '#a'))).toEqual({ left: 100, top: 90, right: 172, bottom: 134 });
	});

	it('measures the insets from the padding box, so a border is not counted twice', () => {
		const root = tree('<button id="b" style="border:2px solid">Go</button>');
		rect(q(root, '#b'), { left: 0, top: 0, right: 36, bottom: 36 });
		extend(q(root, '#b'), 6);
		expect(hitBox(q(root, '#b'))).toEqual({ left: -4, top: -4, right: 40, bottom: 40 });
	});

	it('reads a tap-target inset handed back as the specified min()/calc(), not a used px', () => {
		const root = tree('<a id="a" href="/x">View all</a>');
		rect(q(root, '#a'), { left: 0, top: 0, right: 36, bottom: 36 });
		// What an engine returns for `inset: min(0px, calc(50% - 22px))` before resolving it.
		// 50% of 36 is 18, so each side extends 4px and the hit box is 44.
		const specified = 'min(0px, calc(50% - 22px))';
		pseudo(q(root, '#a'), '::before', {
			content: '',
			position: 'absolute',
			top: specified,
			right: specified,
			bottom: specified,
			left: specified
		});
		expect(hitBox(q(root, '#a'))).toEqual({ left: -4, top: -4, right: 40, bottom: 40 });
		expect(auditTouchTargets(root)).toEqual([]);
	});

	it('ignores a pseudo-element that is not positioned, or has no content', () => {
		const root = tree('<button id="b">Go</button>');
		rect(q(root, '#b'), { left: 0, top: 0, right: 30, bottom: 30 });
		pseudo(q(root, '#b'), '::before', { content: '""', position: 'static', top: '-20px' });
		pseudo(q(root, '#b'), '::after', { content: 'none', position: 'absolute', top: '-20px' });
		expect(hitBox(q(root, '#b'))).toEqual({ left: 0, top: 0, right: 30, bottom: 30 });
	});
});

describe('auditTouchTargets — small', () => {
	it('reports a control under 44px on either axis, with its hit size', () => {
		const root = tree('<a id="a" href="/x">View all</a><button id="b">Go</button>');
		rect(q(root, '#a'), { left: 0, top: 0, right: 72, bottom: 24 });
		rect(q(root, '#b'), { left: 0, top: 100, right: 40, bottom: 150 });
		const found = auditTouchTargets(root);
		expect(found.map((f) => [f.kind, f.element.id, f.width, f.height])).toEqual([
			['small', 'a', 72, 24],
			['small', 'b', 40, 50]
		]);
	});

	it('is quiet at 44×44 and within the tolerance', () => {
		const root = tree('<button id="b">Go</button><button id="c">Go</button>');
		rect(q(root, '#b'), { left: 0, top: 0, right: 44, bottom: 44 });
		rect(q(root, '#c'), { left: 100, top: 0, right: 143.2, bottom: 43.5 });
		expect(auditTouchTargets(root)).toEqual([]);
	});

	it('credits an invisible extension (`tap-target`, the switch’s after:)', () => {
		const root = tree('<a id="a" href="/x">View all</a><button id="s" role="switch"></button>');
		rect(q(root, '#a'), { left: 0, top: 0, right: 72, bottom: 24 });
		extend(q(root, '#a'), 10);
		rect(q(root, '#s'), { left: 0, top: 100, right: 32, bottom: 118 });
		pseudo(q(root, '#s'), '::after', {
			content: '""',
			position: 'absolute',
			top: '-13px',
			bottom: '-13px',
			left: '-12px',
			right: '-12px'
		});
		expect(auditTouchTargets(root)).toEqual([]);
	});

	it('credits the label that wraps and activates a control', () => {
		const root = tree('<label id="l"><button id="s" role="switch"></button> Emails</label>');
		rect(q(root, '#l'), { left: 0, top: 0, right: 200, bottom: 48 });
		rect(q(root, '#s'), { left: 4, top: 15, right: 36, bottom: 33 });
		expect(auditTouchTargets(root)).toEqual([]);
	});

	it('skips what is not a target: hidden, disabled, aria-hidden, tabindex −1, sr-only, waived', () => {
		const root = tree(
			[
				'<button id="hidden" style="display:none">x</button>',
				'<button id="invisible" style="visibility:hidden">x</button>',
				'<button id="disabled" disabled>x</button>',
				'<a id="aria" href="/x" aria-hidden="true">x</a>',
				'<span id="inert" role="button" tabindex="-1">x</span>',
				'<a id="sr" href="/x">x</a>',
				'<a id="ok" href="/x" data-tap-ok>x</a>',
				'<div data-tap-ok><a id="ok2" href="/x">x</a></div>'
			].join('')
		);
		for (const id of ['hidden', 'invisible', 'disabled', 'aria', 'inert', 'ok', 'ok2']) {
			rect(q(root, `#${id}`), { left: 0, top: 0, right: 20, bottom: 20 });
		}
		rect(q(root, '#sr'), { left: 0, top: 0, right: 1, bottom: 1 });
		expect(auditTouchTargets(root)).toEqual([]);
	});

	it('exempts a link that is a word in a sentence, not one standing alone', () => {
		const root = tree(
			'<p id="p">Looking for something? <a id="in" href="/m" style="display:inline">Message your adviser</a>.</p>' +
				'<p id="q"><a id="alone" href="/m" style="display:inline">Message your adviser</a></p>'
		);
		rect(q(root, '#in'), { left: 0, top: 0, right: 150, bottom: 20 });
		rect(q(root, '#alone'), { left: 0, top: 50, right: 150, bottom: 70 });
		expect(auditTouchTargets(root).map((f) => f.element.id)).toEqual(['alone']);
	});

	it('honours a different minimum', () => {
		const root = tree('<button id="b">Go</button>');
		rect(q(root, '#b'), { left: 0, top: 0, right: 30, bottom: 30 });
		expect(auditTouchTargets(root, { min: 24 })).toEqual([]);
		expect(auditTouchTargets(root, { min: 48 })).toHaveLength(1);
	});
});

describe('auditTouchTargets — overlap', () => {
	it('reports an extension reaching into a neighbour’s visible box', () => {
		const root = tree('<button id="a">A</button><button id="b">B</button>');
		// 36px icons at gap-0.5: each extends 4px, 2px of it into the other.
		rect(q(root, '#a'), { left: 0, top: 0, right: 36, bottom: 36 });
		rect(q(root, '#b'), { left: 38, top: 0, right: 74, bottom: 36 });
		extend(q(root, '#a'), 4);
		extend(q(root, '#b'), 4);
		const found = auditTouchTargets(root);
		expect(found.map((f) => [f.kind, f.element.id, f.other?.id])).toEqual([
			['overlap', 'a', 'b'],
			['overlap', 'b', 'a']
		]);
	});

	it('is quiet when the extensions only touch, or only touch each other', () => {
		const root = tree('<button id="a">A</button><button id="b">B</button>');
		// gap-1: each 4px extension reaches exactly the other's edge; gap-2: they meet midway.
		rect(q(root, '#a'), { left: 0, top: 0, right: 36, bottom: 36 });
		rect(q(root, '#b'), { left: 40, top: 0, right: 76, bottom: 36 });
		extend(q(root, '#a'), 4);
		extend(q(root, '#b'), 4);
		expect(auditTouchTargets(root)).toEqual([]);
		rect(q(root, '#b'), { left: 44, top: 0, right: 80, bottom: 36 });
		extend(q(root, '#a'), 4);
		extend(q(root, '#b'), 4);
		expect(auditTouchTargets(root)).toEqual([]);
	});

	it('does not compare nested controls, or visible boxes that already overlap', () => {
		const root = tree(
			'<a id="row" href="/n"><span id="x" role="button" tabindex="0">×</span></a>' +
				'<button id="p">P</button><button id="q">Q</button>'
		);
		rect(q(root, '#row'), { left: 0, top: 0, right: 300, bottom: 60 });
		rect(q(root, '#x'), { left: 250, top: 8, right: 294, bottom: 52 });
		extend(q(root, '#x'), 4);
		// p sits on top of q (a floating control over a card): not an extension's trespass.
		rect(q(root, '#p'), { left: 0, top: 100, right: 44, bottom: 144 });
		rect(q(root, '#q'), { left: 20, top: 100, right: 200, bottom: 144 });
		extend(q(root, '#p'), 4);
		expect(auditTouchTargets(root)).toEqual([]);
	});
});

describe('marks and report', () => {
	it('marks each finding by kind and clears the previous run’s marks', () => {
		const root = tree('<button id="a">A</button><button id="b">B</button>');
		rect(q(root, '#a'), { left: 0, top: 0, right: 20, bottom: 20 });
		rect(q(root, '#b'), { left: 100, top: 0, right: 144, bottom: 44 });
		markTouchTargets(root, auditTouchTargets(root));
		expect(q(root, '#a').getAttribute('data-tap')).toBe('small');
		expect(q(root, '#b').hasAttribute('data-tap')).toBe(false);
		rect(q(root, '#a'), { left: 0, top: 0, right: 44, bottom: 44 });
		markTouchTargets(root, auditTouchTargets(root));
		expect(q(root, '#a').hasAttribute('data-tap')).toBe(false);
	});

	it('logs one collapsed group with a line per finding, and nothing when clean', () => {
		const group = vi.spyOn(console, 'groupCollapsed').mockImplementation(() => {});
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		const end = vi.spyOn(console, 'groupEnd').mockImplementation(() => {});
		reportTouchTargets([], '/x');
		expect(group).not.toHaveBeenCalled();

		const root = tree('<button id="a">A</button><button id="b">B</button>');
		rect(q(root, '#a'), { left: 0, top: 0, right: 36, bottom: 36 });
		rect(q(root, '#b'), { left: 38, top: 0, right: 74, bottom: 36 });
		extend(q(root, '#a'), 4);
		reportTouchTargets(auditTouchTargets(root), '/x');
		expect(group).toHaveBeenCalledWith('[touch] 2 findings on /x');
		expect(warn.mock.calls.map((c) => c[0])).toEqual([
			'small: 36×36 — div > button#b',
			'overlap: hit area reaches a neighbour — div > button#a'
		]);
		expect(end).toHaveBeenCalledOnce();
	});
});
