/**
 * The border family, collapsed into three rows over the rail's five composites: the shorthand
 * `border` plus the four `border-{side}` (`css-borders.ts::borderSideProperty`). Writes go through
 * those composites, never `addStyle()`, so each declaration is a non-detached composite's own
 * `border-{side}: W S C`; the MOUNTING EDITOR's GrapesJS setup holds the checks against collapsing
 * the models into longhands.
 *
 * ONE STYLE AND ONE COLOUR FOR THE WHOLE BOX is the limit. The evidence is from the email path,
 * where every per-side border in the corpus is one side as a shorthand (a note box's
 * `border-left:3px solid #3b82f6`, a divider's `border-top`) with no `border-*-color`/`-style`
 * longhand anywhere; a width of 0 draws nothing, so a stripe is still expressible. That is a claim
 * about authored content rather than about CSS, so a surface whose corpus disagrees — a print
 * document with a two-tone rule, say — is the reason to revisit it, not a bug in this file.
 *
 * A blank width clears that edge's whole composite and `style: none` switches the box off. `border`
 * is absorbed, not pruned — a pasted `border: 1px solid #ccc` would be unreadable AND unclearable —
 * and is written whenever the four widths agree.
 */

import {
	BORDER_ANCHOR,
	BORDER_SIDES,
	BORDER_STYLE_DEFAULT,
	BORDER_WIDTH_DEFAULT
} from './css-borders.js';
import type { PartValues } from '../../ui/box-sides/box-sides.js';
import type { PropertyView } from './read.js';

export const BORDER_FAMILY: readonly string[] = [
	BORDER_ANCHOR,
	...BORDER_SIDES.map((side) => `border-${side}`)
];

/** Matched on the sub-property's CSS NAME by SUFFIX, never its id: GrapesJS ids `border`'s parts
 *  `border-width-sub` (grapes.mjs :66340) where ours are `border-left-width`, so an id lookup reads
 *  blank over half the family. */
export type BorderRole = 'width' | 'style' | 'colour';

const ROLE_SUFFIX: Record<BorderRole, string> = {
	width: '-width',
	style: '-style',
	// `-color`, not `-colour`: this matches the CSS property name, American whatever the labels say.
	colour: '-color'
};

export function borderSub(
	view: PropertyView | undefined,
	role: BorderRole
): PropertyView | undefined {
	return view?.subs.find((sub) => sub.name.endsWith(ROLE_SUFFIX[role]));
}

/** Any member may be absent: a renamed or dropped composite costs that control, not the section. */
export interface BorderFamily {
	all?: PropertyView;
	sides: Partial<Record<string, PropertyView>>;
}

export function isBorderFamily(view: PropertyView): boolean {
	return BORDER_FAMILY.includes(view.name);
}

export function borderFamily(views: readonly PropertyView[]): BorderFamily {
	const family: BorderFamily = { sides: {} };
	for (const view of views) {
		if (view.name === BORDER_ANCHOR) family.all = view;
		else if (isBorderFamily(view)) family.sides[view.name.replace('border-', '')] = view;
	}
	return family;
}

/** `widths` is keyed by side name in CSS shorthand order, which `BoxSides` reads as its own order. */
export interface BorderState {
	widths: PartValues;
	style: string;
	colour: string;
}

const text = (value: string | undefined): string => (value ?? '').trim();

/** `set`, not non-empty: `getValue()` falls back to the DEFAULT, so untouched width/style answer
 *  `1px`/`solid` and would box a `border-top`-only cell. Unit pre-joined, else `border-right: 2 solid`. */
export function held(sub: PropertyView | undefined): string {
	return sub?.set ? text(sub.value) : '';
}

function shared(family: BorderFamily, role: BorderRole): string {
	for (const side of BORDER_SIDES) {
		const value = held(borderSub(family.sides[side], role));
		if (value) return value;
	}
	return held(borderSub(family.all, role));
}

/** A side with no width of its own inherits the all-sides one, as the inbox does: under `border: 1px
 *  solid #ccc` every edge IS 1px. Inheriting a DEFAULT is the opposite mistake — see `held`. */
export function readBorderState(family: BorderFamily): BorderState {
	const allWidth = held(borderSub(family.all, 'width'));
	const widths: PartValues = {};
	for (const side of BORDER_SIDES)
		widths[side] = held(borderSub(family.sides[side], 'width')) || allWidth;
	return { widths, style: shared(family, 'style'), colour: shared(family, 'colour') };
}

/** A width of `0` in any unit means "no border here", so it counts absent and `borderPlan` clears the
 *  declaration rather than leaving `border-left: 0 solid …`. An unparseable width counts PRESENT. */
export function borderWidthPresent(value: string | undefined): boolean {
	const raw = text(value);
	if (raw === '') return false;
	const number = Number.parseFloat(raw);
	return Number.isNaN(number) ? true : number !== 0;
}

export type BorderEdit = BorderRole;

/** One instruction against one composite, `values` keyed by ROLE: the caller resolves each to a
 *  sub-property and writes through the model, so a plan is assertable without an editor. */
export type BorderAction =
	| { target: PropertyView; clear: true }
	| { target: PropertyView; values: Partial<Record<BorderRole, string>> };

/** `via` is consulted for one rule: a style or colour with no width draws nothing, so a colour picked
 *  on a border-less element means "give me a border" at `BORDER_WIDTH_DEFAULT` on every side — while
 *  zeroing the last width must NOT resurrect one round the element being cleared. */
export function borderPlan(
	family: BorderFamily,
	next: BorderState,
	via: BorderEdit
): BorderAction[] {
	const widths: PartValues = { ...next.widths };
	const anyWidth = BORDER_SIDES.some((side) => borderWidthPresent(widths[side]));
	if (!anyWidth && via !== 'width' && (next.style || next.colour))
		for (const side of BORDER_SIDES) widths[side] = BORDER_WIDTH_DEFAULT;

	const on = BORDER_SIDES.filter((side) => borderWidthPresent(widths[side]));
	const members = [family.all, ...BORDER_SIDES.map((side) => family.sides[side])];
	const clearAll = (): BorderAction[] =>
		members
			.filter((view): view is PropertyView => !!view)
			.map((target) => ({ target, clear: true }));

	// Nothing left to draw, so every member goes, `border` included — a border with no width is none.
	if (!on.length) return clearAll();

	const style = next.style || BORDER_STYLE_DEFAULT;
	// THE COLOUR IS ALWAYS WRITTEN, blank included. `apply` iterates only the keys it is handed, so
	// omitting a blank one left the PREVIOUS colour on the element: Clear in the picker did nothing
	// at all, the row still read as set, and there was no route back to the composite's default.
	// Writing `''` is how `clearBorderPart` unsets a part in this same file, so it is the
	// established mechanism rather than a new one.
	//
	// The composite does still fill its own `black` on the way out (`getStyleFromProps` joins
	// `withDefault`) — but that is a property of the READ, and it never justified skipping the write.
	const parts = { style, colour: next.colour || '' };

	const uniform =
		on.length === BORDER_SIDES.length &&
		new Set(BORDER_SIDES.map((side) => text(widths[side]))).size === 1;

	// Uniform ⇒ the all-sides shorthand, one declaration instead of four — only if the rail has `border`.
	if (uniform && family.all) {
		const actions: BorderAction[] = BORDER_SIDES.map((side) => family.sides[side])
			.filter((view): view is PropertyView => !!view)
			.map((target) => ({ target, clear: true }));
		return [...actions, { target: family.all, values: { width: text(widths.top), ...parts } }];
	}

	const actions: BorderAction[] = family.all ? [{ target: family.all, clear: true }] : [];
	for (const side of BORDER_SIDES) {
		const target = family.sides[side];
		if (!target) continue;
		if (on.includes(side))
			actions.push({ target, values: { width: text(widths[side]), ...parts } });
		else actions.push({ target, clear: true });
	}
	return actions;
}

/** Not `clearAll`: unsetting the colour returns the composite to its visible `black` default, where
 *  unsetting a width would silently delete the border. */
export function clearBorderPart(family: BorderFamily, role: BorderRole): BorderAction[] {
	return [family.all, ...BORDER_SIDES.map((side) => family.sides[side])]
		.filter((view): view is PropertyView => !!view)
		.map((target) => ({ target, values: { [role]: '' } as Partial<Record<BorderRole, string>> }));
}

/** The family becomes ONE row at its first member's position, so the editor's sector configuration can reorder it freely. */
export type PanelRow =
	| { kind: 'property'; key: string; view: PropertyView }
	| { kind: 'borders'; key: string; family: BorderFamily };

export function panelRows(views: readonly PropertyView[]): PanelRow[] {
	const family = borderFamily(views);
	const rows: PanelRow[] = [];
	let placed = false;
	for (const view of views) {
		if (!isBorderFamily(view)) {
			rows.push({ kind: 'property', key: view.id, view });
			continue;
		}
		if (placed) continue;
		placed = true;
		rows.push({ kind: 'borders', key: 'borders', family });
	}
	return rows;
}
