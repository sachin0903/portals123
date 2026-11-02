/** Splitting and rejoining a CSS dimension for `UnitInput`. Anything but a number plus an offered unit
 *  is a KEYWORD held verbatim: `auto`, `0`, `calc()` and `{{ tokens }}` all arrive from GrapesJS. */

/** A dimension taken apart. `unit` is null for a keyword and for a unitless number. */
export interface Dimension {
	/** The numeric text, or the whole keyword when there is no number to split off. */
	value: string;
	unit: string | null;
	/** True when `value` is not a number — so the caller knows to hide the unit affordance. */
	keyword: boolean;
}

/** A style model may answer a NUMBER, so values from one are coerced, never asserted — see below. */
export type StyleValue = string | number | null | undefined;

export function valueText(raw: StyleValue): string {
	return String(raw ?? '').trim();
}

/** Not `parseFloat`: the author's `1.50` must not become `1.5`, and the tail has to match a unit. */
const NUMBER = /^([+-]?(?:\d+\.?\d*|\.\d+))(.*)$/;
/** The same number with NOTHING after it — what "may take a unit" actually means. */
const BARE_NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

/** An unrecognised tail is a keyword, never a unit we do not offer: the list is the email-safety
 *  boundary, and `create.ts` gives a border width only `px`/`em` because Word has no viewport unit. */
export function splitDimension(
	raw: string | number | null | undefined,
	units: readonly string[] = []
): Dimension {
	// COERCED, not asserted: GrapesJS answers a NUMBER for a numeric property, and `(raw ?? '').trim()`
	// threw inside a render batch — a control in a SIBLING section never wired up ("the Visibility dropdowns will not open").
	const text = valueText(raw);
	if (text === '') return { value: '', unit: null, keyword: false };
	const match = NUMBER.exec(text);
	if (!match) return { value: text, unit: null, keyword: true };
	const number = match[1] ?? '';
	const tail = match[2] ?? '';
	// A unitless number is legal CSS and common (`padding: 0`), so the unit affordance stays offered.
	if (tail === '') return { value: number, unit: null, keyword: false };
	const unit = units.find((u) => u === tail);
	return unit
		? { value: number, unit, keyword: false }
		: // `100% - 20px` inside a calc, or a unit we do not offer. Held whole.
			{ value: text, unit: null, keyword: true };
}

/** Empty answers empty WHATEVER the unit — what makes the field clearable, since a lone `px` is invalid CSS GrapesJS would keep. */
export function joinDimension(value: StyleValue, unit: string | null | undefined): string {
	const text = valueText(value);
	if (text === '') return '';
	if (!unit) return text;
	// ANCHORED AT BOTH ENDS, and `NUMBER` is not: it ends `(.*)$` so it captures the tail, which made
	// this guard true for anything merely STARTING with a digit. Typing `100%` into a px field
	// committed `100%px`, `splitDimension` then read `%px` as an unknown unit, and the invalid
	// declaration silently unset the property. A value that already carries a unit is passed through
	// whole, exactly as `auto` and `calc()` are.
	return BARE_NUMBER.test(text) ? `${text}${unit}` : text;
}

/** The unit for a value carrying none: the first offered, since a bare number is not a length. */
export function defaultUnit(units: readonly string[]): string | null {
	return units[0] ?? null;
}
