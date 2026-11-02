/**
 * A side panel moves on the house tokens, only on the compositor, and never for a reader who asked
 * for less motion. Duration 0 is what makes Svelte mount and unmount at once, which DOM tests rely
 * on.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { linearEasing, panelSlide } from './panel-motion.js';

/** theme.css's own spring, so the parse is checked against the real token. */
const SMOOTH =
	'linear(0, 0.14 8%, 0.39 18%, 0.62 30%, 0.78 42%, 0.89 55%, 0.95 68%, 0.982 80%, 0.996 90%, 1)';
const TOKENS: Record<string, string> = {
	'--motion-duration-expand': '500ms',
	'--motion-duration-exit': '150ms',
	'--motion-duration-reveal': '220ms',
	'--motion-spring-smooth': SMOOTH
};

/** A node the browser could animate, laid out at a known box. */
function panel(): HTMLElement {
	const props = new Map<string, string>();
	const kebab = (name: string) => name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
	const style = new Proxy({} as CSSStyleDeclaration, {
		get: (_target, key) =>
			key === 'removeProperty'
				? (name: string) => props.delete(name)
				: (props.get(kebab(String(key))) ?? ''),
		set: (_target, key, value) => {
			props.set(kebab(String(key)), String(value));
			return true;
		}
	});
	return {
		animate: () => ({}),
		style,
		getBoundingClientRect: () => ({ left: 1236, top: 120, width: 420, height: 700 }) as DOMRect
	} as unknown as HTMLElement;
}

function page({ reduce = false, tokens = TOKENS } = {}) {
	vi.stubGlobal('matchMedia', (query: string) => ({ matches: reduce && /reduce/.test(query) }));
	vi.stubGlobal('document', { documentElement: {} });
	vi.stubGlobal('getComputedStyle', () => ({
		getPropertyValue: (name: string) => tokens[name] ?? ''
	}));
}

afterEach(() => vi.unstubAllGlobals());

describe('linearEasing', () => {
	it('reads a CSS linear() curve, hitting its stops and joining them straight', () => {
		const ease = linearEasing(SMOOTH)!;
		expect(ease(0)).toBe(0);
		expect(ease(0.08)).toBeCloseTo(0.14);
		expect(ease(0.42)).toBeCloseTo(0.78);
		expect(ease(0.13)).toBeCloseTo((0.14 + 0.39) / 2);
		expect(ease(1)).toBe(1);
	});

	it('places a stop with no position halfway between its neighbours, as CSS does', () => {
		expect(linearEasing('linear(0, 0.5, 1)')!(0.25)).toBeCloseTo(0.25);
	});

	it('is null for anything that is not a linear() curve', () => {
		expect(linearEasing('cubic-bezier(0.2, 0, 0, 1)')).toBeNull();
		expect(linearEasing('')).toBeNull();
	});
});

describe('panelSlide', () => {
	it('opens on the expand duration, the travel on the spring, the opacity on the reveal', () => {
		page();
		const open = panelSlide(panel(), {}, { direction: 'in' });
		expect(open.duration).toBe(500);
		expect(open.css?.(0, 1)).toBe('opacity: 0; transform: translateX(40px)');
		// 10% in (50ms) the panel is already plainly there: an ease-out answers the click.
		const early = open.css?.(0.1, 0.9) ?? '';
		expect(Number(/opacity: ([\d.]+)/.exec(early)![1])).toBeGreaterThan(0.5);
		// 30% in, the spring has covered 62% of the way, as theme.css's curve says.
		const mid = open.css?.(0.3, 0.7) ?? '';
		expect(Number(/translateX\(([\d.]+)px\)/.exec(mid)![1])).toBeCloseTo((1 - 0.62) * 40);
		expect(open.css?.(1, 0)).toBe('opacity: 1; transform: translateX(0px)');
	});

	it('lifts a closing panel out of the layout, and an open puts it back', () => {
		page();
		const node = panel();
		const close = panelSlide(node, {}, { direction: 'out' });
		expect(close.duration).toBe(150);
		expect(node.style.position).toBe('fixed');
		expect([node.style.left, node.style.top, node.style.width]).toEqual([
			'1236px',
			'120px',
			'420px'
		]);
		panelSlide(node, {}, { direction: 'in' });
		expect(node.style.position).toBe('');
		expect(node.style.width).toBe('');
	});

	it('keeps working on a page without the theme’s tokens', () => {
		page({ tokens: {} });
		const open = panelSlide(panel(), {}, { direction: 'in' });
		expect(open.duration).toBe(500);
		expect(open.css?.(1, 0)).toBe('opacity: 1; transform: translateX(0px)');
	});

	it('is instant for a reader who asked for less motion', () => {
		page({ reduce: true });
		expect(panelSlide(panel())).toEqual({ duration: 0 });
	});

	it('is instant where the browser cannot animate an element', () => {
		page();
		expect(panelSlide({} as Element)).toEqual({ duration: 0 });
	});
});
