/** The rules behind `BoxSides`. PARTS, NOT SIDES: GrapesJS's `padding` is the four sides but
 *  `border-radius` is the four CORNERS, so the caller declares its parts, and their order and identity come from the values record's own key order — no parallel key list to keep in step. */

import { valueText } from '../unit-input/dimension.js';

/** The four sides in CSS shorthand order — clockwise from the top. */
export const SIDE_ORDER = ['top', 'right', 'bottom', 'left'] as const;

export type Side = (typeof SIDE_ORDER)[number];

export interface BoxPart {
	key: string;
	label: string;
}

export const SIDE_PARTS: readonly BoxPart[] = SIDE_ORDER.map((key) => ({ key, label: key }));

export type PartValues = Record<string, string>;

/** WHICH PART OF THE BOX a part names, so a field can wear a glyph instead of the word. Read out of the
 *  key and label rather than looked up: the keys are GrapesJS property ids
 *  (`border-top-left-radius-sub`), so nothing here can hold a table of them. A corner answers a
 *  two-letter short form — Lucide has no per-corner icon, and inventing one from an arrow would read
 *  as a direction. `null` for a part that names no side at all, which keeps its label. */
export type PartGlyph = { kind: 'side'; side: Side } | { kind: 'corner'; short: string } | null;

export function partGlyph(part: BoxPart): PartGlyph {
	// Split into WORDS, so `border-top-left-radius-sub` yields both of its axes and `gap` neither.
	const words = new Set(`${part.key} ${part.label}`.toLowerCase().split(/[^a-z]+/));
	const vertical = words.has('top') ? 'top' : words.has('bottom') ? 'bottom' : '';
	const horizontal = words.has('left') ? 'left' : words.has('right') ? 'right' : '';
	if (vertical && horizontal)
		return { kind: 'corner', short: `${vertical[0]}${horizontal[0]}`.toUpperCase() };
	const side = vertical || horizontal;
	return side ? { kind: 'side', side: side as Side } : null;
}

/** Every part holding the same string. An all-blank or empty record counts as linked — nothing is set, so there is no difference to preserve and split would be N empty fields. */
export function sidesLinked(values: PartValues): boolean {
	const all = Object.values(values);
	const first = all[0] ?? '';
	return all.every((v) => (v ?? '') === first);
}

/** The one value for linked mode, `''` when the parts differ: showing a `10px 24px` box as `10px` claims the whole box is that, and the author's next edit overwrites sides they never saw. */
export function commonSide(values: PartValues): string {
	return sidesLinked(values) ? (Object.values(values)[0] ?? '') : '';
}

/** The same parts, every one set to `value`. Keys and order come from `values`, so nothing drifts. */
export function allSides(values: PartValues, value: string): PartValues {
	const next: PartValues = {};
	for (const key of Object.keys(values)) next[key] = value;
	return next;
}

/** True when any part carries a value — what the "is this property set" cue keys off. */
export function anySideSet(values: PartValues): boolean {
	return Object.values(values).some((v) => valueText(v) !== '');
}

/** A summary (`10px · 24px · …`), a blank part held open as `—` so the positions stay countable. It is the control's `title` and not a row of its own, which only restated the fields below it. */
export function describeSides(values: PartValues): string {
	return Object.values(values)
		.map((v) => valueText(v) || '—')
		.join(' · ');
}

/** The one value a COLLAPSE chooses: the first part that HAS one, NOT the first part — a one-sided box (the Note Box's `border-left: 3px solid #3b82f6`) has a blank Top, so `values.top` wiped the author's only value. All-blank answers `''`. */
export function firstSide(values: PartValues): string {
	const all = Object.values(values).map((v) => valueText(v));
	return all.find((v) => v !== '') ?? '';
}

/** A shorthand taken apart: 1 value → all four sides, 2 → vertical then horizontal, 3 → top, horizontal, bottom, 4 → clockwise from the top.
 *  Here rather than in the panel because the PAGE is not selectable: it has no property models, and its inset is a plain string (`create.ts::readPageStyle`). */
export function splitSides(shorthand: string | null | undefined): PartValues {
	const parts = String(shorthand ?? '')
		.trim()
		.split(/\s+/)
		.filter((p) => p !== '');
	const [a, b, c, d] = parts;
	const pick = (): [string, string, string, string] => {
		switch (parts.length) {
			case 1:
				return [a as string, a as string, a as string, a as string];
			case 2:
				return [a as string, b as string, a as string, b as string];
			case 3:
				return [a as string, b as string, c as string, b as string];
			case 4:
				return [a as string, b as string, c as string, d as string];
			default:
				// None, or more than CSS allows: unset rather than silently truncated to three of four edges.
				return ['', '', '', ''];
		}
	};
	const [top, right, bottom, left] = pick();
	return { top, right, bottom, left };
}

/** The shortest shorthand that means the same thing. A blank side among set ones becomes `0` — three sides and a gap is not expressible — and all-blank answers `''`, so the caller can clear it. */
export function joinSides(values: PartValues): string {
	const read = (side: Side): string => (values[side] ?? '').trim();
	const raw = SIDE_ORDER.map(read);
	if (raw.every((v) => v === '')) return '';
	const [top, right, bottom, left] = raw.map((v) => (v === '' ? '0' : v));
	if (top === right && right === bottom && bottom === left) return top as string;
	if (top === bottom && right === left) return `${top} ${right}`;
	if (right === left) return `${top} ${right} ${bottom}`;
	return `${top} ${right} ${bottom} ${left}`;
}
