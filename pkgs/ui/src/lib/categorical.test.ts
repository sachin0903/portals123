import { describe, expect, it } from 'vitest';
import {
	DEFAULT_CARD_DESIGNS,
	FALLBACK_FINISH,
	FINISHES,
	SERIES_BRAND_STEPS,
	accountFace,
	designFor,
	finish,
	isPaleFinish,
	series,
	topicFinish
} from './categorical.js';

describe('card finishes', () => {
	it('never rotate the brand hue — every finish keeps the firm’s own hue', () => {
		for (const f of FINISHES) {
			const css = finish(f);
			expect(css, f).not.toMatch(/calc\(h [+-]/);
			expect(css, f).not.toContain('undefined');
		}
	});

	it('pin brand and deep to the scheme’s lightness knobs, after a plain ink fallback', () => {
		for (const [f, knob] of [
			['brand', '--finish-brand-from'],
			['deep', '--finish-deep-from']
		] as const) {
			const css = finish(f);
			expect(css).toContain(`oklch(from var(--brand) var(${knob})`);
			// The fallback must come first so an engine without relative colour keeps it.
			expect(css.indexOf('var(--hero-from)')).toBeLessThan(css.indexOf('oklch(from'));
		}
	});

	it('gives the pale finishes the page ink, and the dark ones the hero ink', () => {
		for (const f of FINISHES) {
			const css = finish(f);
			if (isPaleFinish(f)) {
				expect(css, f).toContain('--hero-foreground:var(--foreground)');
				expect(css, f).toContain('--hero-positive:var(--positive-strong)');
			} else {
				expect(css, f).not.toContain('--hero-foreground');
			}
		}
		expect(FINISHES.filter(isPaleFinish)).toEqual(['tint', 'paper']);
	});

	it('draws a hairline round the pale finishes so they separate from the page', () => {
		expect(finish('paper')).toContain('inset 0 0 0 1px var(--border)');
		expect(finish('tint')).toContain('inset 0 0 0 1px var(--border)');
	});

	it('falls back to ink for a missing finish', () => {
		expect(finish(null)).toBe(finish('ink'));
		expect(FALLBACK_FINISH).toBe('ink');
	});
});

describe('account faces', () => {
	it('give the four commonest wrappers four different finishes by default', () => {
		const finishes = ['ISA', 'SIPP', 'GIA', 'JISA'].map((t) => DEFAULT_CARD_DESIGNS[t]?.finish);
		expect(new Set(finishes).size).toBe(4);
		expect(Object.keys(DEFAULT_CARD_DESIGNS).sort()).toEqual(['GIA', 'ISA', 'JISA', 'SIPP']);
	});

	it('prefer the firm’s design, then the product default, then ink', () => {
		const designs = { ISA: { finish: 'paper' as const } };
		expect(designFor('ISA', designs)).toEqual({ finish: 'paper' });
		expect(designFor('SIPP', designs)).toEqual(DEFAULT_CARD_DESIGNS.SIPP);
		expect(designFor('Offshore bond', designs)).toEqual({ finish: 'ink' });
		expect(accountFace('ISA', designs)).toBe(finish('paper'));
		expect(accountFace('Offshore bond')).toBe(finish('ink'));
	});

	it('paints artwork over the finish, with the ink the design asks for', () => {
		const art = '/_assets/card-art-0123456789abcdef.png';
		const light = accountFace('GIA', { GIA: { art_url: art } });
		expect(light).toContain(`background-image:url("${art}")`);
		expect(light).toContain('background-size:cover');
		// GIA's default finish is tint (page ink) — artwork defaults to light ink regardless.
		expect(light).not.toContain('--hero-foreground');
		const dark = accountFace('ISA', { ISA: { art_url: art, ink: 'dark' } });
		expect(dark).toContain('--hero-foreground:var(--hero-base)');
	});

	it('a hex replaces the preset, and a pale one carries the page ink', () => {
		const dark = accountFace('ISA', { ISA: { finish: 'brand', color: '#112233' } });
		expect(dark).toContain('background-color:#112233');
		expect(dark).toContain('color-mix(in oklch,#112233 84%,white)');
		expect(dark).not.toContain('oklch(from var(--brand)');
		expect(dark).not.toContain('--hero-foreground');

		const pale = accountFace('GIA', { GIA: { color: '#fde68a' } });
		expect(pale).toContain('background-color:#fde68a');
		expect(pale).toContain('--hero-foreground:var(--foreground)');
		expect(pale).toContain('inset 0 0 0 1px var(--border)');
	});

	it('ignores a colour that could break out of the declaration, and keeps the preset', () => {
		const css = accountFace('ISA', { ISA: { finish: 'deep', color: '#112233;color:red' } });
		expect(css).toBe(finish('deep'));
		expect(css).not.toContain('color:red');
	});

	it('artwork still covers a custom colour', () => {
		const art = '/_assets/card-art-0123456789abcdef.png';
		const css = accountFace('ISA', { ISA: { color: '#112233', art_url: art } });
		expect(css).toContain(`url("${art}")`);
		expect(css).not.toContain('background-color:#112233');
	});

	it('never interpolates an artwork source outside the allowlist', () => {
		const css = accountFace('ISA', {
			ISA: { finish: 'deep', art_url: 'https://x/a.png"); } body{display:none' }
		});
		expect(css).toBe(finish('deep'));
		expect(css).not.toContain('url(');
	});
});

describe('topicFinish', () => {
	it('gives the commonest topic the brand finish and wraps past five', () => {
		expect(topicFinish(0)).toBe('brand');
		expect(new Set([0, 1, 2, 3, 4].map(topicFinish)).size).toBe(5);
		expect(topicFinish(5)).toBe(topicFinish(0));
		expect(topicFinish(-1)).toBe(topicFinish(4));
	});
});

describe('series()', () => {
	it('is a tonal ramp of the brand, then the neutral', () => {
		for (let slot = 0; slot < SERIES_BRAND_STEPS; slot++) {
			expect(series(slot)).toMatch(/^oklch\(from var\(--brand\) [0-9.]+ .* h\)$/);
		}
		expect(series(SERIES_BRAND_STEPS)).toBe('var(--muted-foreground)');
		expect(series(99)).toBe('var(--muted-foreground)');
	});

	it('separates its brand steps by lightness', () => {
		const ls = Array.from({ length: SERIES_BRAND_STEPS }, (_, i) =>
			Number(/var\(--brand\) ([0-9.]+)/.exec(series(i))?.[1])
		);
		const sorted = [...ls].sort((a, b) => a - b);
		for (let i = 1; i < sorted.length; i++) {
			expect((sorted[i] ?? 0) - (sorted[i - 1] ?? 0)).toBeGreaterThanOrEqual(0.13);
		}
	});
});
