/**
 * The typing behaviour of `MoneyInput`, extracted so it can be tested.
 *
 * Formatting an amount AS IT IS TYPED is not the same problem as formatting a settled one, and it
 * is the one that goes wrong: inserting a thousands separator moves every character after it, so
 * a digit typed into the middle of a figure lands in the wrong place unless the caret is
 * re-anchored. That arithmetic is the reason live grouping was avoided in the first version of the
 * component, and it is exactly the kind of thing that has to be pinned rather than eyeballed —
 * `data-table/sort.ts` is here for the same reason.
 */

/** Thousands separators for a run of digits. */
export const groupDigits = (digits: string) => digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

/** A SETTLED amount, at the currency's exact precision: 1234500 → "1,234,500.00".
 *
 *  `toFixed` alone gave "1234500.00", which is the figure a fee or a transfer value is easiest to
 *  misread by a factor of ten. */
export function settled(n: number | null | undefined, precision: number): string {
	if (n == null || !Number.isFinite(n)) return '';
	// Constructed per call, not hoisted: `precision` is a prop, and a zero-decimal currency (JPY,
	// KRW) must not keep being formatted to two.
	return new Intl.NumberFormat('en-GB', {
		minimumFractionDigits: precision,
		maximumFractionDigits: precision
	}).format(n);
}

/**
 * An amount MID-ENTRY. `settled` cannot do this job: it pads to the full precision, so typing "1"
 * would become "1.00" and the next keystroke would land after the pence.
 *
 * So this formats what is actually there and nothing more:
 *   - the integer part is grouped, which is the whole point — an unseparated "1234500" is the
 *     figure that gets misread by a factor of ten, and it is misread while it is being typed
 *   - a trailing decimal point survives, because "1." is a state you must pass through to reach
 *     "1.5"
 *   - pence are neither padded nor rounded: "1.5" stays "1.5" until the field is left, or the 0
 *     in "1.50" could never be typed
 *   - a second decimal point is dropped rather than accepted ("1.2.3" is not a number)
 *   - digits past the precision are REFUSED, not rounded: rounding a figure someone is halfway
 *     through writing changes the amount under them
 *   - a minus sign survives only where a negative is representable (`min` below zero)
 */
export function liveFormat(raw: string, options: { precision: number; signed?: boolean }): string {
	const { precision, signed = false } = options;
	const sign = signed && raw.trimStart().startsWith('-') ? '-' : '';
	const cleaned = raw.replace(/[^\d.]/g, '');
	const dot = cleaned.indexOf('.');
	const whole = groupDigits(dot === -1 ? cleaned : cleaned.slice(0, dot));
	if (dot === -1 || precision === 0) return sign + whole;
	const pence = cleaned
		.slice(dot + 1)
		.replace(/\./g, '')
		.slice(0, precision);
	return `${sign}${whole}.${pence}`;
}

/** How many characters the caret is anchored BEHIND — digits and the decimal point, because those
 *  are what the user is positioned relative to. Separators are not: they move. */
export const significant = (s: string) => (s.match(/[\d.]/g) ?? []).length;

/** Where the caret goes in `display` so that it still sits behind `anchor` significant
 *  characters. Zero means the start — after any minus sign, since nobody wants the caret in
 *  front of it. */
export function caretFor(display: string, anchor: number): number {
	if (anchor <= 0) return display.startsWith('-') ? 1 : 0;
	let seen = 0;
	for (let i = 0; i < display.length; i++) {
		if (/[\d.]/.test(display.charAt(i))) {
			seen += 1;
			if (seen === anchor) return i + 1;
		}
	}
	return display.length;
}

/** The number behind a display string, or null for "no amount". Grouping and the currency symbol
 *  are stripped, so a pasted "£1,234.50" is accepted rather than rejected. Null rather than 0
 *  because a blank fee and a fee of zero are different facts.
 *
 *  Fed the output of `liveFormat`, never a raw keystroke: that is what guarantees there is no
 *  second decimal point or interior minus left to turn into NaN. */
export function parseAmount(
	raw: string,
	bounds: { min?: number; max?: number } = {}
): number | null {
	const cleaned = raw.replace(/[^\d.-]/g, '');
	if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
	const parsed = Number(cleaned);
	if (!Number.isFinite(parsed)) return null;
	return Math.min(bounds.max ?? Infinity, Math.max(bounds.min ?? -Infinity, parsed));
}
