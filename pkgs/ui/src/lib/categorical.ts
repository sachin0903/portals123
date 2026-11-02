/**
 * CARD FINISHES — how an account card (and an insight cover) is painted.
 *
 * This used to be a categorical HUE scale: the firm's --brand rotated by a fixed step per product
 * type, so ISA was teal, SIPP gold, GIA indigo. It told the cards apart and it was off-brand by
 * construction — a firm that chose navy and gold got magenta and green on its home page, because
 * the scale's whole job was to leave the brand hue. Card issuers solve the same problem the other
 * way round: one brand, several FINISHES (the bank's green card, its black card, its paper card),
 * and the product name on the card. That is this file.
 *
 * A finish is a recipe, not a colour. Every one is built off the firm's own tokens, so re-branding
 * re-paints every card and a firm never picks a card colour that clashes with its palette:
 *
 *   ink    the hero ink (`--hero-from/to`) — the deliberate dark anchor
 *   brand  the brand hue, pinned to a mid-dark lightness so white ink always reads
 *   deep   the brand hue, pinned darker and quieter
 *   tint   a pale wash of the brand hue, carrying the page ink
 *   paper  the card surface with a hairline, carrying the page ink
 *
 * The lightness pins are numbers in theme.css (`--finish-*`), so the scheme flips them without the
 * recipe knowing which scheme it is in.
 *
 * Ink: every surface that paints a face reads its text colours from the HERO tokens
 * (`--hero-foreground`, `--hero-positive`, `--hero-negative`), because the stock faces were all
 * dark. A pale finish re-points those three on the element itself, so the deck's markup — dozens of
 * `text-[var(--hero-foreground)]/55` classes — reads correctly on any finish without knowing it.
 */
import {
	colorToHex,
	hexToOklch,
	isCardColor,
	isSafeCardArt,
	type CardDesign,
	type CardFinish
} from '@graphene/auth';

/** The finishes, in the order the studio offers them. */
export const FINISHES: readonly CardFinish[] = ['ink', 'brand', 'deep', 'tint', 'paper'];

/** Plain-language names for the studio. */
export const FINISH_LABELS: Readonly<Record<CardFinish, string>> = {
	ink: 'Ink',
	brand: 'Brand',
	deep: 'Deep',
	tint: 'Tint',
	paper: 'Paper'
};

/** Finishes that carry the page ink rather than white. */
const PALE: ReadonlySet<CardFinish> = new Set(['tint', 'paper']);

/**
 * The product defaults: one finish per wrapper a client actually holds, chosen so the four
 * commonest never share one. A product type not listed here wears `FALLBACK_FINISH`.
 */
export const DEFAULT_CARD_DESIGNS: Readonly<Record<string, CardDesign>> = {
	ISA: { finish: 'brand' },
	SIPP: { finish: 'ink' },
	GIA: { finish: 'tint' },
	JISA: { finish: 'paper' }
};

export const FALLBACK_FINISH: CardFinish = 'ink';

/** Light (white) ink: the hero tokens as the theme declares them. Nothing to override. */
const LIGHT_INK = '';

/** Page ink, for the pale finishes. Follows the scheme: a tint or paper card in dark mode is a dim
 *  surface, and the page's (light) foreground is the ink that reads on it. */
const PAGE_INK =
	'--hero-foreground:var(--foreground);--hero-positive:var(--positive-strong);' +
	'--hero-negative:var(--negative-strong);--hero-accent:var(--brand);color:var(--foreground);';

/** Fixed dark ink, for artwork the firm marked as light. An image is the same in both schemes, so
 *  its ink cannot follow the scheme the way PAGE_INK does. */
const DARK_ART_INK =
	'--hero-foreground:var(--hero-base);--hero-positive:oklch(from var(--positive) 0.42 c h);' +
	'--hero-negative:oklch(from var(--negative) 0.42 c h);color:var(--hero-base);';

/** The hero ink ramp — the `ink` finish, and the fallback paint for the relative-colour ones. */
const INK_RAMP =
	'background-color:var(--hero-via);' +
	'background-image:linear-gradient(135deg,var(--hero-from),var(--hero-to));';

/** A two-stop ramp of the brand hue at two pinned lightnesses. */
function brandRamp(from: string, to: string, chroma: string): string {
	const stop = (l: string) => `oklch(from var(--brand) var(${l}) ${chroma} h)`;
	return `background-image:linear-gradient(135deg,${stop(from)},${stop(to)});`;
}

const HAIRLINE = 'box-shadow:inset 0 0 0 1px var(--border),var(--elev-1);';

/** The background of one finish, without its ink. */
function paint(f: CardFinish): string {
	switch (f) {
		case 'brand':
			return INK_RAMP + brandRamp('--finish-brand-from', '--finish-brand-to', 'c');
		case 'deep':
			return INK_RAMP + brandRamp('--finish-deep-from', '--finish-deep-to', 'calc(c * 0.8)');
		case 'tint':
			return (
				'background-color:var(--muted);' +
				'background-color:oklch(from var(--brand) var(--finish-tint-l) calc(c * var(--finish-tint-c)) h);' +
				'background-image:none;' +
				HAIRLINE
			);
		case 'paper':
			return 'background-color:var(--card);background-image:none;' + HAIRLINE;
		default:
			return INK_RAMP;
	}
}

/** Whether a finish carries the page ink (as opposed to white). */
export function isPaleFinish(f: CardFinish | null | undefined): boolean {
	return !!f && PALE.has(f);
}

/** A custom colour lighter than this carries the page ink; at or below it, white. The same
 *  lightness the hero uses to flip its own foreground. */
const LIGHT_CARD = 0.62;

/** A hex (or any colour without its own parentheses) can sit inside a color-mix. A function
 *  colour is used flat — wrapping it would nest parentheses. */
function customPaint(color: string): string {
	const ground = color.includes('(')
		? `background-color:${color};background-image:none;`
		: `background-color:${color};` +
			`background-image:linear-gradient(135deg,color-mix(in oklch,${color} 84%,white),color-mix(in oklch,${color} 80%,black));`;
	const parsed = hexToOklch(colorToHex(color));
	const pale = !!parsed && parsed.l >= LIGHT_CARD;
	return ground + (pale ? HAIRLINE + PAGE_INK : '');
}

/**
 * Inline declarations painting one finish, ink included. Emit as the element's `style`.
 *
 * The relative-colour finishes are declared twice, the ink ramp first: an engine without relative
 * colour syntax (pre-Baseline 2023) drops the second declaration and keeps a dark, readable card.
 */
export function finish(f: CardFinish | null | undefined): string {
	const known = f ?? FALLBACK_FINISH;
	return paint(known) + (isPaleFinish(known) ? PAGE_INK : LIGHT_INK);
}

/** The design a product type wears: the firm's own, else the product default, else ink. */
export function designFor(
	type: string,
	designs?: Readonly<Record<string, CardDesign>> | null
): CardDesign {
	return designs?.[type] ?? DEFAULT_CARD_DESIGNS[type] ?? { finish: FALLBACK_FINISH };
}

/**
 * The `style` attribute for an account surface of the given product type.
 *
 * Artwork paints over everything else (the finish stays as the fill while the image loads, and
 * wherever it fails), and its ink comes from the design rather than the finish: a firm uploading
 * a light image marks it `ink: 'dark'`. A custom `color` paints instead of the finish. An artwork
 * source or a colour outside the branding allowlist is ignored, never interpolated.
 */
export function accountFace(
	type: string,
	designs?: Readonly<Record<string, CardDesign>> | null
): string {
	const design = designFor(type, designs);
	const base = design.finish ?? DEFAULT_CARD_DESIGNS[type]?.finish ?? FALLBACK_FINISH;
	if (isSafeCardArt(design.art_url)) {
		return (
			paint(base) +
			`background-image:url("${design.art_url}");background-size:cover;background-position:center;` +
			(design.ink === 'dark' ? DARK_ART_INK : LIGHT_INK)
		);
	}
	if (isCardColor(design.color)) return customPaint(design.color);
	return finish(base);
}

/** Topic order for insight covers: the commonest topic takes the brand itself. */
const TOPIC_FINISHES: readonly CardFinish[] = ['brand', 'ink', 'tint', 'deep', 'paper'];

/** The finish an insight topic wears, by its slot (`topicSlots()`); wraps past five. */
export function topicFinish(slot: number): CardFinish {
	const n = TOPIC_FINISHES.length;
	return TOPIC_FINISHES[((slot % n) + n) % n] ?? FALLBACK_FINISH;
}

/**
 * One SOLID colour per slot, for a chart series (donut slices, bar series, section dots).
 *
 * A tonal ramp of the brand, not a hue wheel: brand, a lighter step, a darker step, a pale step,
 * then the neutral. A chart's series are told apart by lightness and by the legend beside them,
 * which keeps every chart on-brand. Past the ramp everything is the neutral rather than a sixth
 * near-duplicate of the brand.
 *
 * Returns a single value (not a declaration pair) because it is consumed as an SVG `fill`.
 */
const SERIES_STEPS = [
	'oklch(from var(--brand) 0.55 calc(max(c, 0.06) * 1.1) h)',
	'oklch(from var(--brand) 0.72 calc(max(c, 0.06) * 0.8) h)',
	'oklch(from var(--brand) 0.4 calc(max(c, 0.06) * 0.9) h)',
	'oklch(from var(--brand) 0.85 calc(max(c, 0.06) * 0.5) h)'
] as const;
const SERIES_NEUTRAL = 'var(--muted-foreground)';

export function series(slot: number): string {
	return SERIES_STEPS[slot] ?? SERIES_NEUTRAL;
}

/** How many brand steps `series` has before it falls to the neutral. */
export const SERIES_BRAND_STEPS = SERIES_STEPS.length;
