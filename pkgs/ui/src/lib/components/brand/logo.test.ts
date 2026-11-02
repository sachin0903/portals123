import { describe, expect, it } from 'vitest';
import { MAX_INLINE_SHELL_BYTES, logoSources, shellSafeSrc } from './logo-sources.js';

const LIGHT = 'data:image/svg+xml;base64,LIGHT';
const DARK = 'data:image/svg+xml;base64,DARK';
const ICON = 'data:image/svg+xml;base64,ICON';

/** A base64 payload that decodes to (at most) `bytes` — 4 characters per 3 bytes. */
function inlineRaster(type: string, bytes: number): string {
	return `data:image/${type};base64,${'A'.repeat(Math.floor(bytes / 3) * 4)}`;
}
const HUGE_PNG = inlineRaster('png', 2_500_000); // the staging logo: a 3481×624 PNG, inlined
const SMALL_PNG = inlineRaster('png', 4_096);

describe('shellSafeSrc', () => {
	/** The reported LCP: a multi-megapixel data URI in a 28px slot, decoded on the main thread. */
	it('refuses a large inline PNG/JPEG/WebP', () => {
		expect(shellSafeSrc(HUGE_PNG)).toBeUndefined();
		expect(shellSafeSrc(inlineRaster('jpeg', MAX_INLINE_SHELL_BYTES + 3))).toBeUndefined();
		expect(shellSafeSrc(inlineRaster('webp', MAX_INLINE_SHELL_BYTES + 3))).toBeUndefined();
	});

	it('passes small inline rasters, inline SVG of any size, URLs and proxy paths', () => {
		expect(shellSafeSrc(SMALL_PNG)).toBe(SMALL_PNG);
		expect(shellSafeSrc(inlineRaster('png', MAX_INLINE_SHELL_BYTES))).toBeDefined();
		const bigSvg = `data:image/svg+xml;base64,${'A'.repeat(4_000_000)}`;
		expect(shellSafeSrc(bigSvg)).toBe(bigSvg);
		expect(shellSafeSrc('/_assets/logo-0123456789abcdef.png')).toBe(
			'/_assets/logo-0123456789abcdef.png'
		);
		expect(shellSafeSrc('https://cdn.example/logo.png')).toBe('https://cdn.example/logo.png');
		expect(shellSafeSrc(undefined)).toBeUndefined();
	});
});

describe('logoSources', () => {
	it('swaps to the dark logo in the wordmark variant', () => {
		expect(logoSources({ variant: 'full', src: LIGHT, darkSrc: DARK })).toEqual({
			img: LIGHT,
			dark: DARK,
			usingIconAsset: false
		});
	});

	/** The reported bug. `logo_variant: 'icon'` with NO icon asset and both logos set — the stock
	 *  Graphene tenant — fell back to the full logo for LIGHT but dropped DARK, so the light logo
	 *  rendered in both schemes. The two fallbacks must move together. */
	it('still swaps when the mark variant falls back to the full logo', () => {
		expect(logoSources({ variant: 'mark', src: LIGHT, darkSrc: DARK })).toEqual({
			img: LIGHT,
			dark: DARK,
			usingIconAsset: false
		});
	});

	it('drops the swap only when a dedicated icon asset is in play', () => {
		// The branding model has no `icon_dark_url`, so a firm's symbol has one form.
		expect(logoSources({ variant: 'mark', src: LIGHT, darkSrc: DARK, iconSrc: ICON })).toEqual({
			img: ICON,
			dark: undefined,
			usingIconAsset: true
		});
	});

	it('ignores an icon asset in the wordmark variant', () => {
		expect(logoSources({ variant: 'full', src: LIGHT, darkSrc: DARK, iconSrc: ICON })).toEqual({
			img: LIGHT,
			dark: DARK,
			usingIconAsset: false
		});
	});

	it('renders no image at all when the firm has uploaded nothing', () => {
		// An undefined `img` is what makes Logo fall through to the built-in Graphene mark, whose
		// gradient is token-driven and so adapts to the scheme on its own.
		expect(logoSources({}).img).toBeUndefined();
		expect(logoSources({ variant: 'mark' }).img).toBeUndefined();
	});

	it('serves one image for both schemes when only a light logo is set', () => {
		const r = logoSources({ variant: 'full', src: LIGHT });
		expect(r.img).toBe(LIGHT);
		expect(r.dark).toBeUndefined();
	});

	it('never emits a large inline raster as the img src — the built-in mark instead', () => {
		expect(logoSources({ variant: 'full', src: HUGE_PNG, darkSrc: HUGE_PNG })).toEqual({
			img: undefined,
			dark: undefined,
			usingIconAsset: false
		});
	});

	it('screens each asset before the fallback rules, so an oversized icon yields the logo', () => {
		expect(logoSources({ variant: 'mark', src: LIGHT, darkSrc: DARK, iconSrc: HUGE_PNG })).toEqual({
			img: LIGHT,
			dark: DARK,
			usingIconAsset: false
		});
		// ...and a small inline raster is still a perfectly good logo.
		expect(logoSources({ variant: 'full', src: SMALL_PNG }).img).toBe(SMALL_PNG);
	});
});
