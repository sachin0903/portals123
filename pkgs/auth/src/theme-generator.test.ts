import { describe, expect, it } from 'vitest';
import {
	generateTheme,
	heroInk,
	applyTheme,
	applyAccents,
	hexToOklch,
	contrastRatio,
	THEME_STYLE_KEYS,
	THEME_PRESETS
} from './theme-generator';
import { COLOUR_VARS_KEYS } from './branding';
import type { Branding, BrandingColors } from './branding';

// Every generated colour must parse as oklch(...) so the API validator accepts it.
const OKLCH = /^oklch\([0-9.]+ [0-9.]+ [0-9.]+\)$/;
// A colour string → LCH for contrast checks (parses our generated `oklch(l c h)`).
function toLch(v: string) {
	const m = /^oklch\(([0-9.]+) ([0-9.]+) ([0-9.]+)\)$/.exec(v);
	if (!m) return hexToOklch('#000000')!;
	return { l: +m[1], c: +m[2], h: +m[3] };
}

describe('generateTheme', () => {
	it('fills exactly the basic palette for light and dark', () => {
		// Everything else aliases these in theme.css, so it tracks the generated palette for free.
		const t = generateTheme('#4338ca', '#6366f1', 'classic');
		for (const scheme of [t.light, t.dark] as BrandingColors[]) {
			expect(Object.keys(scheme).sort()).toEqual([...COLOUR_VARS_KEYS].sort());
			for (const key of COLOUR_VARS_KEYS) {
				expect(scheme[key as keyof BrandingColors], `${key} set`).toBeTruthy();
			}
		}
	});

	// Each of these is the BASE of a derived -subtle/-strong/-line trio in theme.css, built with
	// hue-preserving relative syntax — so if the generator emits the wrong hue here, six tonal tokens
	// go with it. Status meaning is not a matter of taste: caution is amber, info is blue.
	it('keeps status hues semantic regardless of the seed colours', () => {
		const expected = { positive: 162, negative: 25, warning: 71, info: 245 } as const;
		// Deliberately hostile seeds: hot pink + lime should not drag "warning" off amber.
		for (const [p, a] of [
			['#ec4899', '#84cc16'],
			['#4338ca', '#6366f1']
		]) {
			const t = generateTheme(p, a, 'classic');
			for (const scheme of [t.light, t.dark] as BrandingColors[]) {
				for (const [key, hue] of Object.entries(expected)) {
					const got = toLch(scheme[key as keyof BrandingColors]!);
					expect(got.h, `${key} hue (seed ${p})`).toBeCloseTo(hue, 0);
					expect(got.c, `${key} chroma (seed ${p})`).toBeGreaterThan(0.05);
				}
			}
		}
	});

	it('desaturates status colours under the mono style but holds their hue', () => {
		const mono = generateTheme('#4338ca', '#6366f1', 'mono').light;
		const classic = generateTheme('#4338ca', '#6366f1', 'classic').light;
		for (const key of ['warning', 'info', 'positive', 'negative'] as const) {
			expect(toLch(mono[key]!).c, `${key} mono chroma`).toBeLessThan(toLch(classic[key]!).c);
			expect(toLch(mono[key]!).h, `${key} mono hue`).toBeCloseTo(toLch(classic[key]!).h, 0);
		}
	});

	// A solid status fill's ink is picked in theme.css by the base's own lightness (near-white below
	// L 0.6, a dark ink above), so every generated base must read against the ink it will get. The
	// floor is 4.0 rather than 4.5 because the generator reproduces the design system's OWN bases
	// (light positive/info sit at L 0.56/0.58, measuring ~4.2). The point of the guard is that no
	// style/seed combination can drop below it.
	it('keeps every status colour readable under the ink theme.css gives it', () => {
		const inkFor = (base: { l: number; h: number }) => ({
			l: base.l < 0.6 ? 0.985 : 0.2,
			c: 0.01,
			h: base.h
		});
		for (const style of THEME_STYLE_KEYS) {
			const t = generateTheme('#ec4899', '#84cc16', style);
			for (const name of ['light', 'dark'] as const) {
				for (const key of ['positive', 'negative', 'warning', 'info'] as const) {
					const base = toLch(t[name][key]!);
					const ratio = contrastRatio(base, inkFor(base));
					expect(ratio, `${key}/${style}/${name} contrast`).toBeGreaterThanOrEqual(4);
				}
			}
		}
	});

	it('produces readable text on primary in both schemes (WCAG ≥ 4.5)', () => {
		for (const style of THEME_STYLE_KEYS) {
			for (const [p, a] of [
				['#4338ca', '#6366f1'],
				['#f59e0b', '#f97316'], // bright/pale seeds — the hard case
				['#166534', '#22c55e']
			]) {
				const t = generateTheme(p, a, style);
				for (const scheme of [t.light, t.dark]) {
					const ratio = contrastRatio(toLch(scheme.primary!), toLch(scheme.primary_foreground!));
					expect(ratio, `${p}/${style} primary contrast`).toBeGreaterThanOrEqual(4.5);
				}
			}
		}
	});

	it('every solid generated colour is a valid oklch() string', () => {
		const t = generateTheme('#0f766e', '#10b981', 'bold');
		for (const v of Object.values(t.light)) {
			if (v && !v.includes('/')) expect(v).toMatch(OKLCH); // the dark border uses alpha, skipped
		}
	});

	it('mono style desaturates and turns glass off; bold keeps glass on', () => {
		expect(generateTheme('#4338ca', '#6366f1', 'mono').glass.enabled).toBe(false);
		expect(generateTheme('#4338ca', '#6366f1', 'bold').glass.enabled).toBe(true);
	});

	it('keeps a brand colour’s hue instead of rewriting it toward the paper', () => {
		// A pale gold used to be clamped into a tint of the same hue as the background, so the
		// colour a firm set read as a similar colour already in the ground.
		const pale = generateTheme('#fde68a', '#0f766e', 'classic');
		const gold = hexToOklch('#fde68a')!;
		const primary = toLch(pale.light.primary!);
		expect(primary.h).toBeCloseTo(gold.h, 0);
		expect(contrastRatio(primary, toLch(pale.light.background!))).toBeGreaterThanOrEqual(3);
		// The paper is neutral — it does not take the gold hue.
		expect(toLch(pale.light.background!).h).not.toBeCloseTo(gold.h, 0);
		expect(toLch(pale.light.background!).c).toBeLessThan(0.01);
	});

	it('keeps primary and accent as themselves in dark mode', () => {
		// Dark mode used to discard the primary and paint both tokens from the accent.
		const t = generateTheme('#1e3a8a', '#f97316', 'classic');
		const navy = hexToOklch('#1e3a8a')!;
		const orange = hexToOklch('#f97316')!;
		expect(toLch(t.dark.primary!).h).toBeCloseTo(navy.h, 0);
		expect(toLch(t.dark.brand!).h).toBeCloseTo(orange.h, 0);
		expect(toLch(t.dark.background!).h).not.toBeCloseTo(navy.h, 0);
	});

	it('heroInk is a deep shade of the seed, never a copy of it', () => {
		const ink = heroInk('#f97316');
		expect(ink).toMatch(/^oklch\(0\.26 /);
		expect(toLch(ink!).h).toBeCloseTo(hexToOklch('#f97316')!.h, 0);
		expect(heroInk('not-a-colour')).toBeNull();
	});

	it('falls back to defaults for bad seeds without throwing', () => {
		const t = generateTheme('not-a-colour', null, 'classic');
		expect(t.light.primary).toMatch(OKLCH);
	});

	it('every preset generates cleanly', () => {
		for (const preset of THEME_PRESETS) {
			const t = generateTheme(preset.primary, preset.accent, preset.style);
			expect(t.light.primary).toMatch(OKLCH);
			expect(t.dark.primary).toMatch(OKLCH);
		}
	});
});

describe('applyAccents', () => {
	it('writes only primary and brand — not paper or status hues', () => {
		const target: Branding = { colors: { light: { background: '#fff0f0', warning: '#b45309' } } };
		applyAccents(target, '#4338ca', '#17a2a2');
		expect(target.colors?.light?.background).toBeUndefined();
		expect(target.colors?.light?.warning).toBeUndefined();
		expect(target.colors?.light?.primary).toMatch(OKLCH);
		expect(target.colors?.light?.brand).toMatch(OKLCH);
		expect(Object.keys(target.colors?.light ?? {}).sort()).toEqual([
			'brand',
			'brand_foreground',
			'primary',
			'primary_foreground'
		]);
		expect(target.colors?.dark?.primary).toMatch(OKLCH);
		expect(target.radius).toBeUndefined();
		expect(target.glass).toBeUndefined();
	});
});

describe('applyTheme', () => {
	it('replaces colours/radius/glass but preserves identity + layout', () => {
		const draft: Branding = {
			logo_url: 'https://cdn/x.svg',
			login: { tagline: 'keep me' },
			layout: [{ type: 'portfolioHeader', visible: true, w: 12, h: 1 }]
		};
		applyTheme(draft, generateTheme('#9d174d', '#ec4899', 'soft'));
		expect(draft.logo_url).toBe('https://cdn/x.svg');
		expect(draft.login?.tagline).toBe('keep me');
		expect(draft.layout?.[0].type).toBe('portfolioHeader');
		expect(draft.colors?.light?.primary).toMatch(OKLCH);
		expect(draft.radius).toBeTruthy();
	});
});
