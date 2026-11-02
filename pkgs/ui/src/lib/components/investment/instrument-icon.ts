/**
 * The placeholder logo for a holding — a lettered disc, until real instrument logos are wired.
 *
 * A holdings table with no mark down its first column reads as a wall of text: every row is a long
 * fund name in the same weight, and nothing gives the eye an anchor to come back to. A logo would
 * fix that and we do not have one (there is no logo source in the book — `platform.instruments`
 * carries an ISIN and a name), so this stands in: a stable two-letter disc, derived from what we DO
 * hold, that a real logo can replace behind the same component with no caller changing.
 *
 * Pure, and separate from the component, so the two rules worth getting right are unit-tested:
 *
 * 1. The LETTERS skip a fund name's furniture. "Vanguard FTSE All-World UCITS ETF Acc" initialled
 *    naively gives VF, and so does "Vanguard FTSE Developed Europe" — two different holdings with
 *    the same disc, which is worse than no disc. Dropping the structural words ("fund", "trust",
 *    "acc", "etf", "plc"…) is what makes the letters name the instrument rather than its wrapper.
 *    This is deliberately NOT the person rule (`documents/display.ts::initials`, and the audit
 *    strip's `initialsOf`): a person's name has no noise words to strip and stripping them from
 *    one would be a bug, which is why this is a third rule rather than a fourth caller of theirs.
 *
 * 2. The TONE is derived from a hash of the identity, so the same instrument is the same colour on
 *    every screen and across reloads — a disc whose colour moved would be worse than a grey one.
 *    Only the neutral-ish brand/info/primary tones are in the ramp: success, warning and
 *    destructive mean PERFORMANCE and STATUS everywhere else in the book, and spending them on an
 *    arbitrary hash would make a green disc read as a gain.
 */

/** The tone tokens a disc may take. Deliberately excludes success/warning/danger — see above. */
export const INSTRUMENT_TONES = ['brand', 'info', 'primary'] as const;

export type InstrumentTone = (typeof INSTRUMENT_TONES)[number];

/** Tone → the classes the disc paints with. Tokens only, never a palette colour (the rule
 *  `theme.css` sets and `RiskBadge` follows), so a firm re-branding re-tones its own discs. */
export const INSTRUMENT_TONE_CLASSES: Record<InstrumentTone | 'neutral', string> = {
	brand: 'bg-brand/12 text-brand',
	info: 'bg-info/12 text-info',
	primary: 'bg-primary/12 text-primary',
	// Cash is not an instrument and does not get a colour of its own: it is the same money in every
	// account, and giving it a hashed tone would imply it varies.
	neutral: 'bg-muted text-muted-foreground'
};

/**
 * A fund name's structural words. Stripped before initialling, so the letters come from the words
 * that distinguish one holding from another. Share classes and accumulation/income suffixes are in
 * here too: "… Acc" and "… Inc" are the same instrument to the eye scanning a column.
 */
const NOISE = new Set([
	'a',
	'acc',
	'accumulation',
	'and',
	'class',
	'de',
	'dist',
	'distributing',
	'etf',
	'fund',
	'funds',
	'gbp',
	'group',
	'hedged',
	'holdings',
	'inc',
	'income',
	'index',
	'inst',
	'institutional',
	'investment',
	'invt',
	'limited',
	'ltd',
	'net',
	'of',
	'ord',
	'plc',
	'portfolio',
	'ret',
	'retail',
	'sicav',
	'the',
	'trust',
	'ucits',
	'unhedged',
	'unit',
	'units'
]);

/** True for a holding that is cash rather than an instrument (the conformed `Cash` position). */
export function isCashHolding(name: string | null | undefined): boolean {
	return (name ?? '').trim().toLowerCase() === 'cash';
}

/**
 * The disc's letters: up to two, from the first significant words of the name.
 *
 * Falls back through the noise filter rather than past it — a name made ENTIRELY of noise words
 * ("The Fund") still gets letters, because an empty disc beside a real holding looks like a
 * rendering failure rather than a missing logo. A one-word name gives its first two letters
 * ("Alphabet" → AL), which reads better than a lone letter in a disc.
 */
export function instrumentInitials(name: string | null | undefined): string {
	const words = (name ?? '')
		.replace(/[^\p{L}\p{N}\s]/gu, ' ')
		.split(/\s+/)
		.filter(Boolean);
	if (!words.length) return '?';
	const significant = words.filter((w) => !NOISE.has(w.toLowerCase()));
	const chosen = significant.length ? significant : words;
	// Indexed reads are `T | undefined` under `noUncheckedIndexedAccess`, and the lengths are
	// already established above — narrowed with locals rather than `!`, so the fallbacks are real.
	const first = chosen[0] ?? '';
	const second = chosen[1];
	if (!second) return first.slice(0, 2).toUpperCase() || '?';
	return ((first[0] ?? '') + (second[0] ?? '')).toUpperCase() || '?';
}

/**
 * The disc's tone, from a stable hash of the instrument's identity.
 *
 * Keyed on the TICKER (the ISIN, for real data) when there is one and the name otherwise, because
 * the ISIN is the identity: two share classes of one fund have different names on different
 * screens and the same disc is the right answer for them. 32-bit FNV-1a — small, dependency-free
 * and stable across platforms, which `Math.random()` and object iteration order are not.
 */
export function instrumentTone(
	name: string | null | undefined,
	ticker?: string | null
): InstrumentTone | 'neutral' {
	if (isCashHolding(name)) return 'neutral';
	const key = (ticker || name || '').trim().toLowerCase();
	if (!key) return 'neutral';
	let hash = 0x811c9dc5;
	for (let i = 0; i < key.length; i++) {
		hash ^= key.charCodeAt(i);
		hash = Math.imul(hash, 0x01000193) >>> 0;
	}
	return INSTRUMENT_TONES[hash % INSTRUMENT_TONES.length] ?? 'neutral';
}
