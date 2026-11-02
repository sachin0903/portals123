import { describe, expect, it } from 'vitest';
import {
	clamp01,
	hexToHsv,
	hsvToHex,
	isHex,
	normaliseHex,
	padPosition,
	isIndirectColour,
	swatchInk
} from './colour';

/** Mostly the REFUSAL to parse: coercing `{{ brand.accent }}` into a hex unbrands every email using it, and nobody notices until a client receives one. */

describe('normaliseHex', () => {
	it('expands the three-digit form and lower-cases', () => {
		expect(normaliseHex('#ABC')).toBe('#aabbcc');
		expect(normaliseHex('#3B82F6')).toBe('#3b82f6');
	});

	it('tolerates surrounding whitespace', () => {
		expect(normaliseHex('  #fff  ')).toBe('#ffffff');
	});

	it('keeps an eight-digit hex whole', () => {
		// The pad edits the RGB half only, but dropping alpha here would discard it on first focus.
		expect(normaliseHex('#3b82f680')).toBe('#3b82f680');
	});

	it('refuses anything that is not a hex', () => {
		expect(normaliseHex('{{ brand.accent }}')).toBeNull();
		expect(normaliseHex('transparent')).toBeNull();
		expect(normaliseHex('inherit')).toBeNull();
		expect(normaliseHex('rgb(1,2,3)')).toBeNull();
		expect(normaliseHex('oklch(0.7 0.1 250)')).toBeNull();
		expect(normaliseHex('#12345')).toBeNull();
		expect(normaliseHex('#gggggg')).toBeNull();
		expect(normaliseHex('')).toBeNull();
		expect(normaliseHex(null)).toBeNull();
		expect(normaliseHex(undefined)).toBeNull();
	});

	it('isHex agrees with it', () => {
		expect(isHex('#fff')).toBe(true);
		expect(isHex('{{ brand.accent }}')).toBe(false);
	});
});

describe('hexToHsv / hsvToHex', () => {
	it('round-trips the primaries exactly', () => {
		// A brand hex one byte out drifts further on every edit, and the drift is invisible on screen.
		for (const hex of ['#ff0000', '#00ff00', '#0000ff', '#ffffff', '#000000', '#3b82f6']) {
			const hsv = hexToHsv(hex);
			expect(hsv).not.toBeNull();
			expect(hsvToHex(hsv!)).toBe(hex);
		}
	});

	it('reads a grey as zero saturation', () => {
		const hsv = hexToHsv('#808080');
		expect(hsv!.s).toBe(0);
		expect(hsv!.h).toBe(0);
	});

	it('reads hue in degrees', () => {
		expect(hexToHsv('#ff0000')!.h).toBeCloseTo(0);
		expect(hexToHsv('#00ff00')!.h).toBeCloseTo(120);
		expect(hexToHsv('#0000ff')!.h).toBeCloseTo(240);
	});

	it('returns null for a non-hex', () => {
		expect(hexToHsv('{{ brand.accent }}')).toBeNull();
		expect(hexToHsv('transparent')).toBeNull();
	});

	it('ignores the alpha half of an eight-digit hex', () => {
		expect(hexToHsv('#ff000080')).toEqual(hexToHsv('#ff0000'));
		expect(hsvToHex(hexToHsv('#ff000080')!)).toBe('#ff0000');
	});

	it('wraps hue rather than clamping it', () => {
		expect(hsvToHex({ h: 360, s: 1, v: 1 })).toBe('#ff0000');
		expect(hsvToHex({ h: 720, s: 1, v: 1 })).toBe('#ff0000');
		expect(hsvToHex({ h: -120, s: 1, v: 1 })).toBe('#0000ff');
	});

	it('clamps saturation and value out of range', () => {
		expect(hsvToHex({ h: 0, s: 2, v: 2 })).toBe('#ff0000');
		expect(hsvToHex({ h: 0, s: -1, v: -1 })).toBe('#000000');
	});
});

describe('clamp01', () => {
	it('holds the 0-1 range', () => {
		expect(clamp01(-0.5)).toBe(0);
		expect(clamp01(0.5)).toBe(0.5);
		expect(clamp01(1.5)).toBe(1);
	});

	it('treats a non-number as zero', () => {
		expect(clamp01(NaN)).toBe(0);
		expect(clamp01(Infinity)).toBe(1);
	});
});

describe('padPosition', () => {
	const rect = { left: 100, top: 50, width: 200, height: 100 };

	it('reads a pointer as a 0-1 pair', () => {
		expect(padPosition(rect, 200, 100)).toEqual({ x: 0.5, y: 0.5 });
		expect(padPosition(rect, 100, 50)).toEqual({ x: 0, y: 0 });
		expect(padPosition(rect, 300, 150)).toEqual({ x: 1, y: 1 });
	});

	it('clamps a pointer dragged outside the pad', () => {
		expect(padPosition(rect, 0, 0)).toEqual({ x: 0, y: 0 });
		expect(padPosition(rect, 999, 999)).toEqual({ x: 1, y: 1 });
	});

	it('survives a zero-size rect', () => {
		// The pad is measured on pointerdown, and a hidden popover measures 0×0.
		expect(padPosition({ left: 0, top: 0, width: 0, height: 0 }, 10, 10)).toEqual({ x: 0, y: 0 });
	});
});

describe('isIndirectColour', () => {
	it('is true for a merge token, which has no colour to paint', () => {
		expect(isIndirectColour('{{ brand.accent }}')).toBe(true);
		expect(isIndirectColour('{% if x %}#fff{% endif %}')).toBe(true);
	});

	it('is true for a keyword that defers to something else', () => {
		expect(isIndirectColour('inherit')).toBe(true);
		expect(isIndirectColour('currentColor')).toBe(true);
		expect(isIndirectColour(' UNSET ')).toBe(true);
	});

	// The swatch used to test `!isHex`, so each of these drew the "not set" hatch — and `black` is the default of all five border-colour controls in the rail.
	it('is false for a fixed colour in any notation, hex or not', () => {
		for (const value of [
			'black',
			'rebeccapurple',
			'#3b82f6',
			'#abc',
			'rgb(0 0 0)',
			'rgba(0,0,0,.5)',
			'hsl(210 40% 50%)',
			'oklch(0.7 0.1 220)',
			'color-mix(in oklch, red, blue)'
		]) {
			expect(isIndirectColour(value)).toBe(false);
		}
	});

	// `transparent` is a real colour with alpha 0, so the hatch showing through the pane is honest.
	it('is false for transparent', () => {
		expect(isIndirectColour('transparent')).toBe(false);
	});

	it('is false for an empty value, which is unset rather than indirect', () => {
		expect(isIndirectColour('')).toBe(false);
		expect(isIndirectColour(null)).toBe(false);
		expect(isIndirectColour(undefined)).toBe(false);
	});
});

describe('swatchInk', () => {
	it('asks for light ink on a dark colour and dark ink on a light one', () => {
		expect(swatchInk('#000000')).toBe('light');
		expect(swatchInk('#ffffff')).toBe('dark');
		expect(swatchInk('#3b82f6')).toBe('light');
	});

	it('falls back to dark ink for a value it cannot measure', () => {
		expect(swatchInk('{{ brand.accent }}')).toBe('dark');
		expect(swatchInk(null)).toBe('dark');
	});
});

describe('a colour value that is not a string', () => {
	// `value.trim()` had no `?? ''`, so a model answering a number threw; a number is not a hex, so it gets the same null every non-hex does.
	it('answers null instead of throwing', () => {
		expect(normaliseHex(16777215 as unknown as string)).toBeNull();
		expect(normaliseHex(0 as unknown as string)).toBeNull();
	});
});
