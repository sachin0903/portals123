/**
 * How a pushed side panel opens and closes: the rail beside a listing that opens when a row is
 * picked (the transactions ledger's detail, the contributions classify panel, the transfers
 * detail). Its contents, when another row is picked, take `motion-item-enter` (theme.css).
 *
 * THE HOUSE VOCABULARY, READ FROM theme.css. A panel is a drawer-sized surface, so it opens on
 * `--motion-duration-expand` and `--motion-spring-smooth` (no overshoot: a bounce on a surface this
 * size reads as instability) and leaves on `--motion-duration-exit`. Opacity does not ride the
 * spring, because a spring moves geometry: it takes `--motion-duration-reveal` on an ease-out, as
 * `motion-item-enter` does, so the panel answers the click at once (on the ease-in-out of
 * `motion-fade` it was 3% visible 150ms in, and read as lag). The tokens are read at run time,
 * so theme.css stays the one place the numbers live; the fallbacks below are only for a page
 * without the theme.
 *
 * ONLY `transform` AND `opacity` MOVE, because those the browser composites without laying the
 * page out again. Animating the panel's width re-laid the whole table out on every frame:
 * measured at 50–117ms a frame against 8–17ms for a slide. So the table changes width once, at the
 * click, and the panel does the moving. On the way out the panel is lifted out of the layout where
 * it stands, so the table takes its room back at once and the panel leaves over it; left in the
 * layout, it held the table narrow until it had gone and the table then jumped wide.
 *
 * Use it as `in:panelSlide out:panelSlide`, not `transition:` — Svelte builds a bidirectional
 * transition once, and the close has to lift the panel when it starts. The `in:` also matters for
 * a close that is interrupted by a reopen: Svelte takes the same panel back, and the open puts it
 * into the layout again.
 *
 * Instant for a reader who asked the OS for less motion, and wherever the Web Animations API is
 * missing (jsdom): Svelte then mounts and unmounts synchronously, so a DOM test that closes a
 * panel finds it gone straight away.
 */
import { cubicOut } from 'svelte/easing';
import type { TransitionConfig } from 'svelte/transition';

/** How far the panel travels from the edge it sits on, unless a caller says otherwise. */
const TRAVEL_PX = 40;

/** Read at call time, never at module scope: jsdom has no `matchMedia`. */
function still(node: Element): boolean {
	if (typeof (node as HTMLElement).animate !== 'function') return true;
	return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function token(name: string): string {
	if (typeof getComputedStyle !== 'function' || typeof document === 'undefined') return '';
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** A duration token ("500ms", "0.5s") in milliseconds, or the fallback. */
function duration(name: string, fallback: number): number {
	const match = /^([\d.]+)(ms|s)$/.exec(token(name));
	if (!match) return fallback;
	const value = Number(match[1]);
	return match[2] === 's' ? value * 1000 : value;
}

/**
 * A CSS `linear()` easing ("linear(0, 0.14 8%, …, 1)") as a function, or null when it is not one.
 * The spring tokens are sampled curves, and a Svelte transition needs its easing in JavaScript.
 * A stop without a position takes the midpoint of its neighbours, as CSS spaces them.
 */
export function linearEasing(value: string): ((t: number) => number) | null {
	const body = /^linear\((.*)\)$/s.exec(value.trim())?.[1];
	if (!body) return null;
	const stops = body.split(',').map((part) => {
		const [output, at] = part.trim().split(/\s+/);
		return { y: Number(output), x: at?.endsWith('%') ? Number(at.slice(0, -1)) / 100 : NaN };
	});
	if (stops.length < 2 || stops.some((s) => Number.isNaN(s.y))) return null;
	stops[0]!.x = Number.isNaN(stops[0]!.x) ? 0 : stops[0]!.x;
	const last = stops[stops.length - 1]!;
	last.x = Number.isNaN(last.x) ? 1 : last.x;
	for (let i = 1; i < stops.length - 1; i++) {
		if (Number.isNaN(stops[i]!.x)) stops[i]!.x = (stops[i - 1]!.x + stops[i + 1]!.x) / 2;
	}
	return (t) => {
		if (t <= stops[0]!.x) return stops[0]!.y;
		for (let i = 1; i < stops.length; i++) {
			const a = stops[i - 1]!;
			const b = stops[i]!;
			if (t <= b.x) return b.x === a.x ? b.y : a.y + ((b.y - a.y) * (t - a.x)) / (b.x - a.x);
		}
		return last.y;
	};
}

/** The inline styles a closing panel is lifted with, and an open clears. */
const LIFT = ['position', 'left', 'top', 'width', 'height', 'z-index', 'pointer-events'] as const;

function lift(panel: HTMLElement): void {
	const box = panel.getBoundingClientRect();
	panel.style.position = 'fixed';
	panel.style.left = `${box.left}px`;
	panel.style.top = `${box.top}px`;
	panel.style.width = `${box.width}px`;
	panel.style.height = `${box.height}px`;
	panel.style.zIndex = '20';
	panel.style.pointerEvents = 'none';
}

function ground(panel: HTMLElement): void {
	for (const property of LIFT) panel.style.removeProperty(property);
}

/** The panel opening and closing. */
export function panelSlide(
	node: Element,
	{ travel = TRAVEL_PX }: { travel?: number } = {},
	{ direction = 'in' }: { direction?: 'in' | 'out' | 'both' } = {}
): TransitionConfig {
	if (still(node)) return { duration: 0 };
	const panel = node as HTMLElement;

	if (direction === 'out') {
		lift(panel);
		return {
			duration: duration('--motion-duration-exit', 150),
			easing: cubicOut,
			css: (t) => `opacity: ${t}; transform: translateX(${(1 - t) * travel}px)`
		};
	}

	ground(panel);
	const total = duration('--motion-duration-expand', 500);
	const spring = linearEasing(token('--motion-spring-smooth')) ?? cubicOut;
	// The fade runs to its own length inside the open, on its own curve.
	const fadeShare = Math.min(1, duration('--motion-duration-reveal', 220) / total);
	return {
		duration: total,
		// Linear here, so `css` sees the clock and can give the travel and the fade their own curves.
		easing: (t) => t,
		css: (t) => {
			const offset = (1 - spring(t)) * travel;
			const opacity = cubicOut(Math.min(1, t / fadeShare));
			return `opacity: ${opacity}; transform: translateX(${offset}px)`;
		}
	};
}
