/**
 * Curated theme generator. From one or two seed colours + a style, it produces a FULL, harmonious,
 * always-readable palette (light + dark) in OKLCH — every foreground is chosen by WCAG contrast, so a
 * firm can't accidentally build an unreadable/ugly portal ("impossible to make ugly, within reason").
 * Output is a partial `Branding` (colours + radius + glass) the editor merges into the draft. The
 * seeds are the whole colour input: there is no per-token override, because every token is either
 * generated here or an alias of one that is.
 *
 * Pure + dependency-free (no culori): sRGB↔OKLab maths inline, unit-tested in theme-generator.test.ts.
 */
import type { Branding, BrandingColors, BrandingGlass } from './branding';
import { FIXED_COLOUR_KEYS } from './branding';

export type Mode = 'light' | 'dark' | 'auto';
export type ThemeStyleKey = 'classic' | 'soft' | 'bold' | 'mono';

interface LCH {
	l: number;
	c: number;
	h: number;
}

// ── colour maths ────────────────────────────────────────────────────────────
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const round = (n: number, d = 4) => Math.round(n * 10 ** d) / 10 ** d;

function srgbToLinear(c: number): number {
	return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** Parse #rgb / #rrggbb into OKLCH. Returns null for anything else (the caller falls back). */
export function hexToOklch(hex: string | null | undefined): LCH | null {
	if (!hex) return null;
	let h = hex.trim().replace(/^#/, '');
	if (h.length === 3)
		h = h
			.split('')
			.map((c) => c + c)
			.join('');
	if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
	const r = srgbToLinear(parseInt(h.slice(0, 2), 16) / 255);
	const g = srgbToLinear(parseInt(h.slice(2, 4), 16) / 255);
	const b = srgbToLinear(parseInt(h.slice(4, 6), 16) / 255);
	const l_ = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
	const m_ = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
	const s_ = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
	const L = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
	const a = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
	const bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;
	const c = Math.sqrt(a * a + bb * bb);
	let hue = (Math.atan2(bb, a) * 180) / Math.PI;
	if (hue < 0) hue += 360;
	return { l: L, c, h: c < 1e-4 ? 0 : hue };
}

/** OKLCH → linear sRGB (r,g,b in 0–1, gamut-clipped). */
function oklchToLinear({ l, c, h }: LCH): [number, number, number] {
	const hr = (h * Math.PI) / 180;
	const a = c * Math.cos(hr);
	const b = c * Math.sin(hr);
	const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
	return [
		clamp(4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_, 0, 1),
		clamp(-1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_, 0, 1),
		clamp(-0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_, 0, 1)
	];
}

/** OKLCH → linear-sRGB relative luminance (for WCAG contrast). */
function luminance(lch: LCH): number {
	const [r, g, b] = oklchToLinear(lch);
	return clamp(0.2126 * r + 0.7152 * g + 0.0722 * b, 0, 1);
}

function linearToSrgb(c: number): number {
	return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
}

/** Parse an `oklch(L C H)` / `oklch(L C H / a)` string into LCH (null otherwise). */
function parseOklch(v: string): LCH | null {
	const m = /^oklch\(\s*([0-9.]+%?)\s+([0-9.]+)\s+([0-9.]+)/.exec(v.trim());
	const lRaw = m?.[1];
	const cRaw = m?.[2];
	const hRaw = m?.[3];
	if (lRaw === undefined || cRaw === undefined || hRaw === undefined) return null;
	const l = lRaw.endsWith('%') ? parseFloat(lRaw) / 100 : parseFloat(lRaw);
	return { l, c: parseFloat(cRaw), h: parseFloat(hRaw) };
}

/** Any supported colour string → `#rrggbb` (for the native colour input). Null if unparseable. */
export function colorToHex(value: string | null | undefined): string | null {
	if (!value) return null;
	const v = value.trim();
	if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase();
	if (/^#[0-9a-fA-F]{3}$/.test(v))
		return (
			'#' +
			v
				.slice(1)
				.split('')
				.map((c) => c + c)
				.join('')
				.toLowerCase()
		);
	const lch = parseOklch(v);
	if (!lch) return null;
	const [r, g, b] = oklchToLinear(lch);
	const hx = (n: number) =>
		Math.round(clamp(linearToSrgb(n), 0, 1) * 255)
			.toString(16)
			.padStart(2, '0');
	return `#${hx(r)}${hx(g)}${hx(b)}`;
}

/** WCAG contrast ratio between two OKLCH colours (1–21). Exported for tests / guards. */
export function contrastRatio(a: LCH, b: LCH): number {
	const la = luminance(a);
	const lb = luminance(b);
	return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const oklch = ({ l, c, h }: LCH) => `oklch(${round(l)} ${round(c, 4)} ${round(h, 2)})`;

/** The most readable foreground (near-white or near-ink, hue-matched) for a surface. */
function foregroundFor(surface: LCH): string {
	const light: LCH = { l: 0.985, c: 0.008, h: surface.h };
	const dark: LCH = { l: 0.2, c: 0.03, h: surface.h };
	return oklch(contrastRatio(surface, light) >= contrastRatio(surface, dark) ? light : dark);
}

// ── styles ──────────────────────────────────────────────────────────────────
export interface ThemeStyle {
	label: string;
	/** Multiplier applied to seed chroma (garish ↔ muted). */
	chroma: number;
	radius: string;
	glass: BrandingGlass;
}

export const THEME_STYLES: Record<ThemeStyleKey, ThemeStyle> = {
	classic: {
		label: 'Classic',
		chroma: 1,
		radius: '0.75rem',
		glass: { enabled: true, opacity: 55, blur: 20, sheen: 60 }
	},
	soft: {
		label: 'Soft',
		chroma: 0.82,
		radius: '1.25rem',
		glass: { enabled: true, opacity: 66, blur: 24, sheen: 45 }
	},
	bold: {
		label: 'Bold',
		chroma: 1.3,
		radius: '0.375rem',
		glass: { enabled: true, opacity: 46, blur: 14, sheen: 78 }
	},
	mono: {
		label: 'Mono',
		chroma: 0.16,
		radius: '0.5rem',
		glass: { enabled: false }
	}
};

export const THEME_STYLE_KEYS = Object.keys(THEME_STYLES) as ThemeStyleKey[];

// ── generation ────────────────────────────────────────────────────────────────
const DEFAULT_PRIMARY: LCH = { l: 0.3, c: 0.055, h: 256 }; // ink navy
const DEFAULT_ACCENT: LCH = { l: 0.64, c: 0.115, h: 202 }; // teal

const CAP_C = 0.17; // keep everything in a sane, non-garish chroma range

/** Paper hue. Neutrals used to take the primary's own hue, so a firm that set a navy got a
 *  navy-tinted page — a second, similar copy of the colour they had just chosen, which is what
 *  made the brand look overridden by the background. The ground is paper. The brand stays the brand. */
const PAPER_H = 250;

/** A solid brand colour that stays the seed. Lightness moves only when the colour would otherwise
 *  disappear into the page ground (a pale seed on white paper, a near-black seed on the dark
 *  canvas) or fail to carry readable ink. The walk goes AWAY from the ground, never towards it,
 *  and the hue is never touched — the colour cannot be rewritten into a cousin of the background. */
function apartFrom(seed: LCH, ground: LCH): LCH {
	const dir = ground.l >= 0.5 ? -1 : 1;
	let l = clamp(seed.l, 0.16, 0.9);
	const at = (lightness: number): LCH => ({ l: lightness, c: seed.c, h: seed.h });
	for (let i = 0; i < 48; i++) {
		const color = at(l);
		const onLight = contrastRatio(color, { l: 0.985, c: 0.008, h: color.h });
		const onDark = contrastRatio(color, { l: 0.2, c: 0.03, h: color.h });
		if (contrastRatio(color, ground) >= 3 && Math.max(onLight, onDark) >= 4.5) return color;
		const next = l + dir * 0.015;
		if (next < 0.16 || next > 0.9) return color;
		l = next;
	}
	return at(l);
}

/** The hero band's ink for a seed: the brand's own hue, lightness pinned dark. A light brand
 *  stays a dark band (it cannot turn white), and a dark brand is deeper than the buttons, so the
 *  two cannot be the same colour. Mirrors the `--hero-base` brandingHead emits. */
export function heroInk(seed: string | null | undefined): string | null {
	const parsed = hexToOklch(colorToHex(seed));
	if (!parsed) return null;
	return oklch({ l: 0.26, c: clamp(parsed.c * 0.65, 0.03, 0.09), h: parsed.h });
}

/**
 * Status hues are FIXED, not seeded: "caution" must read amber and "information" must read blue in
 * every firm's portal, or a status badge stops carrying meaning. Only their chroma tracks the style
 * (so `mono` desaturates them with everything else) — exactly how positive/negative already work.
 *
 * These match the design system's own bases in pkgs/ui theme.css, and they matter more than the two
 * seeded tokens do: each is the BASE of a derived `-subtle`/`-strong`/`-line` trio built with
 * hue-preserving relative colour syntax, so generating a warning/info at all is what makes
 * `bg-warning-subtle text-warning-strong border-warning-line` amber rather than the design system's
 * amber sitting untouched under a re-themed portal. Six derived tokens hang off these two hues.
 */
const STATUS_HUE = { warning: 71, info: 245 } as const;

/** The light basic palette. Only the fourteen brandable tokens: everything else (popover, card
 *  ink, ring, success/destructive, the status foregrounds, the sidebar) is an alias in theme.css,
 *  so emitting it here would only freeze a copy that stops following the palette. Neutrals are a
 *  fixed cool paper — they do not take the brand hue (see PAPER_H). */
function lightColors(p: LCH, a: LCH, cP: number, cA: number, ch: number): BrandingColors {
	const ground: LCH = { l: 0.985, c: 0, h: PAPER_H };
	const primary = apartFrom({ l: p.l, c: cP, h: p.h }, ground);
	const brand = apartFrom({ l: a.l, c: cA, h: a.h }, ground);
	return {
		background: oklch({ l: 0.985, c: 0.003, h: PAPER_H }),
		foreground: oklch({ l: 0.23, c: 0.012, h: PAPER_H }),
		card: oklch({ l: 1, c: 0, h: 0 }),
		muted: oklch({ l: 0.965, c: 0.004, h: PAPER_H }),
		muted_foreground: oklch({ l: 0.5, c: 0.012, h: PAPER_H }),
		border: oklch({ l: 0.9, c: 0.006, h: PAPER_H }),
		primary: oklch(primary),
		primary_foreground: foregroundFor(primary),
		brand: oklch(brand),
		brand_foreground: foregroundFor(brand),
		// A step under the stock 0.56: at the bold style's chroma, 0.56 left white ink just under 4:1.
		positive: oklch({ l: 0.55, c: clamp(0.12 * ch, 0.06, 0.16), h: 162 }),
		negative: oklch({ l: 0.55, c: clamp(0.17 * ch, 0.09, 0.19), h: 25 }),
		warning: oklch({ l: 0.62, c: clamp(0.13 * ch, 0.07, 0.16), h: STATUS_HUE.warning }),
		info: oklch({ l: 0.58, c: clamp(0.12 * ch, 0.06, 0.16), h: STATUS_HUE.info })
	};
}

function darkColors(p: LCH, a: LCH, cP: number, cA: number): BrandingColors {
	// The dark canvas is neutral too. Primary keeps the primary's hue and brand keeps the
	// accent's — dark mode used to throw the primary away and paint both from the accent, so the
	// colour a firm set survived only in light mode.
	const ground: LCH = { l: 0.18, c: 0, h: PAPER_H };
	const primary = apartFrom({ l: p.l, c: cP, h: p.h }, ground);
	const brand = apartFrom({ l: a.l, c: cA, h: a.h }, ground);
	return {
		background: oklch({ l: 0.18, c: 0.012, h: PAPER_H }),
		foreground: oklch({ l: 0.94, c: 0.008, h: PAPER_H }),
		card: oklch({ l: 0.22, c: 0.014, h: PAPER_H }),
		muted: oklch({ l: 0.27, c: 0.014, h: PAPER_H }),
		muted_foreground: oklch({ l: 0.7, c: 0.012, h: PAPER_H }),
		border: 'oklch(1 0 0 / 12%)',
		primary: oklch(primary),
		primary_foreground: foregroundFor(primary),
		brand: oklch(brand),
		brand_foreground: foregroundFor(brand),
		positive: oklch({ l: 0.74, c: 0.13, h: 162 }),
		negative: oklch({ l: 0.68, c: 0.17, h: 25 }),
		warning: oklch({ l: 0.8, c: 0.13, h: STATUS_HUE.warning }),
		info: oklch({ l: 0.72, c: 0.12, h: STATUS_HUE.info })
	};
}

export interface GeneratedTheme {
	light: BrandingColors;
	dark: BrandingColors;
	radius: string;
	glass: BrandingGlass;
}

/** Generate a full light+dark palette (+radius/glass) from seed colours and a style. */
export function generateTheme(
	seedPrimary: string | null | undefined,
	seedAccent: string | null | undefined,
	styleKey: ThemeStyleKey = 'classic'
): GeneratedTheme {
	const style = THEME_STYLES[styleKey] ?? THEME_STYLES.classic;
	const p = hexToOklch(seedPrimary) ?? DEFAULT_PRIMARY;
	const acc = hexToOklch(seedAccent) ?? DEFAULT_ACCENT;
	const cP = clamp(p.c * style.chroma, 0, CAP_C);
	const cA = clamp(acc.c * style.chroma, 0, CAP_C);
	return {
		light: lightColors(p, acc, cP, cA, style.chroma),
		dark: darkColors(p, acc, cP, cA),
		radius: style.radius,
		glass: { ...style.glass }
	};
}

/** Apply a generated theme onto a Branding draft, preserving identity/copy/layout/mode. */
export function applyTheme(target: Branding, theme: GeneratedTheme): void {
	target.colors = { light: { ...theme.light }, dark: { ...theme.dark } };
	target.radius = theme.radius;
	target.glass = { ...theme.glass };
}

function pickAccents(palette: BrandingColors): BrandingColors {
	const out: BrandingColors = {};
	for (const key of FIXED_COLOUR_KEYS) {
		const value = palette[key];
		if (value) out[key] = value;
	}
	return out;
}

/** Staff-portal colouring: two seeds become primary + brand (the focus ring aliases brand), with
 *  readable foregrounds. Paper, type and material stay the product's. */
export function applyAccents(
	target: Branding,
	seedPrimary: string | null | undefined,
	seedAccent: string | null | undefined
): void {
	const theme = generateTheme(seedPrimary, seedAccent, 'classic');
	target.colors = { light: pickAccents(theme.light), dark: pickAccents(theme.dark) };
}

// ── ready-made presets (the gallery) ─────────────────────────────────────────
export interface ThemePreset {
	name: string;
	primary: string;
	accent: string;
	style: ThemeStyleKey;
	mode: Mode;
}

export const THEME_PRESETS: ThemePreset[] = [
	{ name: 'Graphene', primary: '#2b3a55', accent: '#17a2a2', style: 'classic', mode: 'auto' },
	{ name: 'Emerald', primary: '#0f766e', accent: '#10b981', style: 'soft', mode: 'auto' },
	{ name: 'Indigo', primary: '#4338ca', accent: '#6366f1', style: 'classic', mode: 'auto' },
	{ name: 'Sunset', primary: '#9a3412', accent: '#f97316', style: 'bold', mode: 'auto' },
	{ name: 'Berry', primary: '#9d174d', accent: '#ec4899', style: 'bold', mode: 'auto' },
	{ name: 'Forest', primary: '#166534', accent: '#22c55e', style: 'soft', mode: 'auto' },
	{ name: 'Slate', primary: '#334155', accent: '#64748b', style: 'mono', mode: 'auto' },
	{ name: 'Midnight', primary: '#1e293b', accent: '#38bdf8', style: 'classic', mode: 'dark' }
];

/** A curated set of Google fonts (the font select) — all safe, readable choices. */
export const FONT_OPTIONS = [
	'Inter',
	'Roboto',
	'Open Sans',
	'Lato',
	'Montserrat',
	'Poppins',
	'Work Sans',
	'Source Sans 3',
	'Nunito',
	'IBM Plex Sans',
	'Playfair Display',
	'Merriweather'
];
