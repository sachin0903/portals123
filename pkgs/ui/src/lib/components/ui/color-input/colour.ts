/** THE VALUE IS AN ARBITRARY CSS COLOUR STRING, not a hex: `transparent`, `inherit` and a
 *  `{{ brand.accent }}` token merged at send are all real answers, so every function here passes through what it cannot parse — coercing one unbrands every email that used it. */

import { valueText, type StyleValue } from '../unit-input/dimension.js';

/** Hue 0-360, saturation and value 0-1. The shape the picker's pad and slider work in. */
export interface Hsv {
	h: number;
	s: number;
	v: number;
}

/** Clamp to 0-1. NaN carries no direction so it answers 0 where an infinity clamps: a hidden popover measures 0x0, and a `NaNpx` gradient stop is invalid CSS the browser drops, blanking the pad. */
export function clamp01(n: number): number {
	if (Number.isNaN(n)) return 0;
	return n < 0 ? 0 : n > 1 ? 1 : n;
}

/** Expand `#abc` to `#aabbcc` and lower-case. Null means "not a hex" — a token, a keyword, an `oklch()`, a typo mid-edit — which every caller reads as "leave the author's string alone". */
export function normaliseHex(value: StyleValue): string | null {
	if (!value) return null;
	// Coerced, not asserted — a style model can answer a number. See `valueText`.
	const raw = valueText(value).toLowerCase();
	const short = /^#([0-9a-f]{3})$/.exec(raw)?.[1];
	if (short) {
		return (
			'#' +
			short
				.split('')
				.map((c) => c + c)
				.join('')
		);
	}
	// 8-digit hex carries alpha: normalised, but the pad edits the RGB half only (`hexToHsv` drops it, `hsvToHex` never writes one back), so a typed alpha survives until the pad is dragged.
	const long = /^#([0-9a-f]{6}([0-9a-f]{2})?)$/.exec(raw)?.[1];
	return long ? `#${long}` : null;
}

/** Whether the value names a colour INDIRECTLY — a `{{ … }}` token merged at send, or a keyword that
 *  defers elsewhere. NOT `!isHex`: `black`, `rgb(0 0 0)` and `oklch(…)` are fixed colours the browser
 *  paints, and hatching those "not set" hit every border colour in the rail, which defaults to `black`.
 *  It asks what a value REFERS to, so the list stays closed and an unheard-of notation needs nothing. */
export function isIndirectColour(value: string | null | undefined): boolean {
	if (!value) return false;
	const raw = valueText(value).trim().toLowerCase();
	// A merge token — the `{{ … }}` case, plus `{% … %}` for a value an author has built with a tag.
	if (raw.includes('{{') || raw.includes('{%')) return true;
	return INDIRECT_KEYWORDS.has(raw);
}

/** Each defers to a value we cannot know here. `transparent` is NOT one: a fixed colour with alpha 0, drawn as such, and the hatch showing through it is right. */
const INDIRECT_KEYWORDS = new Set([
	'inherit',
	'initial',
	'unset',
	'revert',
	'revert-layer',
	'currentcolor'
]);

export function isHex(value: string | null | undefined): boolean {
	return normaliseHex(value) !== null;
}

export function hexToHsv(value: string | null | undefined): Hsv | null {
	const hex = normaliseHex(value);
	if (!hex) return null;
	const r = parseInt(hex.slice(1, 3), 16) / 255;
	const g = parseInt(hex.slice(3, 5), 16) / 255;
	const b = parseInt(hex.slice(5, 7), 16) / 255;
	const max = Math.max(r, g, b);
	const min = Math.min(r, g, b);
	const d = max - min;
	let h = 0;
	// A grey has no hue: 0 rather than NaN keeps the slider somewhere real, and at zero saturation the choice is invisible until the author deliberately moves off grey.
	if (d !== 0) {
		if (max === r) h = ((g - b) / d) % 6;
		else if (max === g) h = (b - r) / d + 2;
		else h = (r - g) / d + 4;
		h *= 60;
		if (h < 0) h += 360;
	}
	return { h, s: max === 0 ? 0 : d / max, v: max };
}

/** Always six digits, lower-case; never an alpha, so the pad cannot invent transparency. */
export function hsvToHex({ h, s, v }: Hsv): string {
	const sat = clamp01(s);
	const val = clamp01(v);
	// Wrap rather than clamp: hue is a circle, so a drag past the end reads 360 meeting 0.
	const hue = ((h % 360) + 360) % 360;
	const c = val * sat;
	const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
	const m = val - c;
	const seg = Math.floor(hue / 60) % 6;
	// Tupled, not indexed out of a nested array: the channels then type as numbers under `noUncheckedIndexedAccess`.
	const wheel: readonly (readonly [number, number, number])[] = [
		[c, x, 0],
		[x, c, 0],
		[0, c, x],
		[0, x, c],
		[x, 0, c],
		[c, 0, x]
	];
	const [r, g, b] = wheel[seg] ?? ([0, 0, 0] as const);
	const byte = (n: number): string =>
		Math.round((n + m) * 255)
			.toString(16)
			.padStart(2, '0');
	return `#${byte(r)}${byte(g)}${byte(b)}`;
}

/** A pointer's place inside `rect` as a 0-1 pair. Shared by the pad (both axes) and the hue slider (x only), so the clamping is written once. */
export function padPosition(
	rect: { left: number; top: number; width: number; height: number },
	clientX: number,
	clientY: number
): { x: number; y: number } {
	return {
		x: clamp01(rect.width === 0 ? 0 : (clientX - rect.left) / rect.width),
		y: clamp01(rect.height === 0 ? 0 : (clientY - rect.top) / rect.height)
	};
}

/** Which ink reads over a swatch, by WCAG relative luminance. A 0.5 threshold rather than a contrast ratio, since the choice is between two fixed inks; a non-hex has none to measure and gets 'dark'. */
export function swatchInk(value: string | null | undefined): 'light' | 'dark' {
	const hsv = hexToHsv(value);
	if (!hsv) return 'dark';
	const hex = normaliseHex(value) as string;
	const chan = (i: number): number => {
		const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
		return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
	};
	const lum = 0.2126 * chan(0) + 0.7152 * chan(1) + 0.0722 * chan(2);
	return lum < 0.5 ? 'light' : 'dark';
}
