import { CURRENCY_SYMBOLS } from './currency-symbols.js';

const GBP = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' });
const GBP_SIGNED = new Intl.NumberFormat('en-GB', {
	style: 'currency',
	currency: 'GBP',
	signDisplay: 'always'
});
const PCT = new Intl.NumberFormat('en-GB', {
	style: 'percent',
	minimumFractionDigits: 1,
	maximumFractionDigits: 1,
	signDisplay: 'always'
});
const PCT_PLAIN = new Intl.NumberFormat('en-GB', {
	style: 'percent',
	minimumFractionDigits: 1,
	maximumFractionDigits: 1
});
const GBP_COMPACT = new Intl.NumberFormat('en-GB', {
	style: 'currency',
	currency: 'GBP',
	notation: 'compact',
	maximumFractionDigits: 1
});
// Billions get their own instance at 2dp rather than sharing GBP_COMPACT's 1dp — "£1.13b" carries
// the precision a nine-figure sum needs, where "£1.1bn" or "£1.1b" would hide ~£30m of it.
const GBP_COMPACT_BILLIONS = new Intl.NumberFormat('en-GB', {
	style: 'currency',
	currency: 'GBP',
	notation: 'compact',
	maximumFractionDigits: 2
});

function normaliseCurrency(code: string | null | undefined): string {
	const trimmed = (code ?? 'GBP').trim().toUpperCase();
	return trimmed || 'GBP';
}

type MoneyFormats = { plain: Intl.NumberFormat; signed: Intl.NumberFormat };

/** One formatter pair per reference currency, built once. `ref.currencies` does not change,
 *  so this map is not added to when a figure arrives in a code the seed does not list. */
const FORMATS: ReadonlyMap<string, MoneyFormats> = new Map(
	Object.keys(CURRENCY_SYMBOLS).flatMap((code) => {
		try {
			return [
				[
					code,
					{
						plain: new Intl.NumberFormat('en-GB', { style: 'currency', currency: code }),
						signed: new Intl.NumberFormat('en-GB', {
							style: 'currency',
							currency: code,
							signDisplay: 'always'
						})
					}
				] as [string, MoneyFormats]
			];
		} catch {
			// A seed code Intl does not recognise still displays; the symbol is applied later.
			return [];
		}
	})
);

/** A code outside the reference table. Not stored: the catalogue is closed. */
function uncachedFormat(code: string, signed: boolean): Intl.NumberFormat {
	try {
		return new Intl.NumberFormat('en-GB', {
			style: 'currency',
			currency: code,
			currencyDisplay: 'narrowSymbol',
			...(signed ? { signDisplay: 'always' as const } : {})
		});
	} catch {
		return signed ? GBP_SIGNED : GBP;
	}
}

/**
 * Parts for an amount, with the currency glyph taken from `ref.currencies` when the code is
 * seeded. Intl's `en-GB` currency style prints `US$` for dollars; the table's symbol is `$`,
 * and the ISO code is a column of its own. A code the seed does not list uses `narrowSymbol`
 * so it still does not say `US$`.
 */
function moneyParts(
	amount: number,
	currency: string | null | undefined,
	signed = false
): Intl.NumberFormatPart[] {
	const code = normaliseCurrency(currency);
	const symbol = CURRENCY_SYMBOLS[code];
	const formats = symbol ? FORMATS.get(code) : undefined;
	const fmt = formats ? (signed ? formats.signed : formats.plain) : uncachedFormat(code, signed);
	const parts = fmt.formatToParts(amount);
	if (!symbol) return parts;
	return parts.map((part) => (part.type === 'currency' ? { ...part, value: symbol } : part));
}

function joinParts(parts: Intl.NumberFormatPart[]): string {
	return parts.map((part) => part.value).join('');
}

/** Format an amount in its own currency, e.g. (1240.5, 'USD') → "$1,240.50". */
export function formatMoney(amount: number, currency = 'GBP'): string {
	return joinParts(moneyParts(amount, currency));
}

/**
 * A dealing price, e.g. (4.6399, 'GBP') → "£4.6399".
 *
 * Two to four places: a unit price is quoted finer than a sum of money, and trailing zeros
 * past the pence are not part of the quote. The glyph is the reference symbol, as `formatMoney`.
 */
export function formatUnitPrice(amount: number, currency = 'GBP'): string {
	const code = normaliseCurrency(currency);
	const symbol = CURRENCY_SYMBOLS[code];
	let parts: Intl.NumberFormatPart[];
	try {
		parts = new Intl.NumberFormat('en-GB', {
			style: 'currency',
			currency: code,
			minimumFractionDigits: 2,
			maximumFractionDigits: 4
		}).formatToParts(Math.abs(amount));
	} catch {
		parts = GBP.formatToParts(Math.abs(amount));
	}
	if (symbol)
		parts = parts.map((part) => (part.type === 'currency' ? { ...part, value: symbol } : part));
	return joinParts(parts);
}

/** Format a signed gain/loss in its own currency, e.g. (2610, 'EUR') → "+€2,610.00". */
export function formatSignedMoney(amount: number, currency = 'GBP'): string {
	return joinParts(moneyParts(amount, currency, true));
}

/** Format pounds as GBP, e.g. 1240.5 → "£1,240.50". */
export function formatGBP(amount: number): string {
	return GBP.format(amount);
}

/** Format a signed gain/loss in GBP, e.g. 2610 → "+£2,610.00". */
export function formatSignedGBP(amount: number): string {
	return GBP_SIGNED.format(amount);
}

/**
 * Compact GBP for dense/space-constrained surfaces, e.g. 1_240_000 → "£1.2m", 860_000 → "£860k",
 * 1_130_000_000 → "£1.13bn". Below `threshold` the exact figure is shown instead, via `formatGBP`
 * — a value that already fits should stay exact rather than lose precision it didn't need to.
 * Pass threshold 0 (the default) to always compact. Billions get their own 2dp instance
 * (`GBP_COMPACT_BILLIONS`) rather than sharing the 1dp millions/thousands one — see its comment.
 */
export function formatGBPCompact(amount: number, threshold = 0): string {
	if (Math.abs(amount) < threshold) return formatGBP(amount);
	const compact = Math.abs(amount) >= 1_000_000_000 ? GBP_COMPACT_BILLIONS : GBP_COMPACT;
	return compact.format(amount).toLowerCase();
}

/**
 * Split a GBP amount into its major part ("£1,240") and minor part (".50"), so dense
 * tables can render the pence smaller and muted — the figure stays scannable down a
 * column while the exact pence remain readable. Built from `formatToParts` rather than
 * a string split so the grouping/decimal separators stay locale-correct.
 */
export function splitMoney(
	amount: number,
	options?: { signed?: boolean; currency?: string | null }
): {
	major: string;
	minor: string;
} {
	const parts = moneyParts(amount, options?.currency, options?.signed);
	const decimal = parts.findIndex((p) => p.type === 'decimal');
	const join = (from: number, to?: number) =>
		parts
			.slice(from, to)
			.map((p) => p.value)
			.join('');
	// No fraction (shouldn't happen for GBP, but a formatter change shouldn't lose the figure).
	if (decimal === -1) return { major: join(0), minor: '' };
	return { major: join(0, decimal), minor: join(decimal) };
}

export function splitGBP(
	amount: number,
	options?: { signed?: boolean }
): {
	major: string;
	minor: string;
} {
	return splitMoney(amount, options);
}

/** Every symbol the app prints (longest first, so "NT$" wins over "$"), with the sign and
 *  the figure: "£1,240.50", "-kr 1.00", "CHF 5.00". Built from the same table as `formatMoney`. */
const STAT_FIGURE = (() => {
	const symbols = [...new Set(Object.values(CURRENCY_SYMBOLS))]
		.sort((x, y) => y.length - x.length)
		.map((symbol) => symbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
	return new RegExp(`^([+\\-\\u2212]?(?:${symbols.join('|')})\\s?\\d[\\d,]*)(\\.\\d{2})$`, 'u');
})();

/**
 * Split a figure a KPI already formatted ("£1,240.50", "+£2,610.00") so the tile can
 * set the pence back. A count, an em dash and a compact "£1.2m" stay one run — only a
 * sum that ends in pence, and carries a currency symbol, is a money figure.
 */
export function splitStatFigure(value: string): { major: string; minor: string } | null {
	const match = STAT_FIGURE.exec(value.trim());
	const major = match?.[1];
	const minor = match?.[2];
	if (major === undefined || minor === undefined) return null;
	return { major, minor };
}

/** Format a fraction as a signed percentage, e.g. 0.042 → "+4.2%". */
export function formatPct(fraction: number): string {
	return PCT.format(fraction);
}

/**
 * Format a fraction as an UNSIGNED percentage, e.g. 0 → "0.0%". For neutral/flat states:
 * `formatPct`'s `signDisplay: 'always'` renders zero as "+0.0%", which presents no change
 * as a gain — a flat or day-one portfolio must not read as "up".
 */
export function formatPctPlain(fraction: number): string {
	return PCT_PLAIN.format(fraction);
}

/**
 * A return percentage is only trustworthy when it's finite and within a sane band — a
 * near-zero cost basis (common in imported data) otherwise yields absurd figures like
 * +1,287%. Returns the fraction when plausible, else `null` (render as "—"). Default cap
 * ±1000% (±10.0). At this calibre a wrong number is worse than no number.
 */
export function sanePct(fraction: number | null | undefined, cap = 10): number | null {
	if (fraction == null || !Number.isFinite(fraction)) return null;
	return Math.abs(fraction) > cap ? null : fraction;
}

/** Fraction digits `formatPct` shows: one decimal of a percent is three of the fraction. */
export const PCT_DIGITS = 3;

/** Fraction digits a currency displays (2 for GBP, 0 for JPY). */
export function minorDigits(currency?: string | null): number {
	const code = normaliseCurrency(currency);
	const fmt = FORMATS.get(code)?.plain ?? uncachedFormat(code, false);
	return fmt.resolvedOptions().maximumFractionDigits ?? 2;
}

/**
 * Which way a figure points AT ITS DISPLAY PRECISION: `null` when there is no figure or it rounds
 * to zero, so a float residue (5.55e-17) or a sub-penny loss never reads as a gain or a loss.
 */
export function gainDirection(value: number | null | undefined, digits = 2): 'up' | 'down' | null {
	if (value == null || !Number.isFinite(value)) return null;
	if (Math.round(Math.abs(value) * 10 ** digits) === 0) return null;
	return value > 0 ? 'up' : 'down';
}

/** The two "good/bad" token families: `money` for the client portal, `directional` for the book. */
export const GAIN_TONES = {
	money: { up: 'text-positive', down: 'text-negative' },
	directional: { up: 'text-success-strong', down: 'text-destructive' }
} as const;

/**
 * The text tone for a gain or loss: muted when there is no figure or it rounds to zero at
 * `digits` (money 2, `PCT_DIGITS` for a `formatPct` figure), else the family's up/down token.
 */
export function gainTone(
	value: number | null | undefined,
	{ digits = 2, family = 'money' }: { digits?: number; family?: keyof typeof GAIN_TONES } = {}
): string {
	const dir = gainDirection(value, digits);
	return dir ? GAIN_TONES[family][dir] : 'text-muted-foreground';
}
