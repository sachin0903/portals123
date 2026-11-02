/** GrapesJS's StyleManager MODELS, read into our own controls. `styleManager: { custom: true }`
 *  suppresses the rail's VIEW only, so every MODEL and all of the mounting editor's own safety work still
 *  runs. Write through `upValue`/`clear`, never `component.addStyle()` — it goes around the composite
 *  owning the declaration and puts two declarations on one edge. */

import { sentenceCase } from '../../../sentence-case.js';

/** GrapesJS's property types (grapes.mjs :66086-66093) plus `base`; a test pins each to a control. */
export const PROPERTY_TYPES = [
	'base',
	'number',
	'color',
	'radio',
	'select',
	'file',
	'slider',
	'composite',
	'stack'
] as const;

/** The subset of GrapesJS's `Property` we use. Duck-typed so tests can build plain fakes. */
export interface PropertyLike {
	getId(): string;
	getName(): string;
	getLabel(): string;
	getType(): string;
	// GrapesJS answers a numeric property with a NUMBER, not its string form; `value` normalises once.
	getValue(opts?: { noDefault?: boolean }): string | number | undefined;
	hasValue(opts?: { noParent?: boolean }): boolean;
	canClear(): boolean;
	upValue(value: string, opts?: object): unknown;
	clear(opts?: object): unknown;
	getUnits?(): string[];
	/** A numeric value's unit: `getValue()` on a `1px` border width answers `1`, the `px` only here. */
	getUnit?(): string;
	getMin?(): number;
	getMax?(): number;
	getStep?(): number;
	getOptions?(): { id: string; label?: string; name?: string }[];
	getOptionId?(option: { id: string }): string;
	getOptionLabel?(id: string): string;
	getProperties?(): PropertyLike[];
	/** Whether this applies to the selection — GrapesJS's own answer (`StyleManager.__upProps`,
	 *  grapes.mjs :67743), how an editor takes the Text sector off an `<img>`. Defaults to `true`. */
	isVisible?(): boolean;
}

export interface SectorLike {
	getId(): string;
	getName(): string;
	isOpen(): boolean;
	getProperties(opts?: { withValue?: boolean }): PropertyLike[];
}

export interface StyleManagerLike {
	getSectors(opts?: { visible?: boolean }): SectorLike[];
}

export interface Choice {
	value: string;
	label: string;
}

export type Control =
	| { kind: 'colour' }
	| { kind: 'length'; units: string[]; min?: number }
	| { kind: 'choice'; options: Choice[] }
	| { kind: 'select'; options: Choice[] }
	| { kind: 'slider'; min: number; max: number; step: number }
	| { kind: 'parts'; parts: { key: string; label: string }[]; units: string[]; min?: number }
	| { kind: 'group' }
	| { kind: 'text' };

export type ControlKind = Control['kind'];

/** A property, ready to render. `property` is the live model the controls write through. */
export interface PropertyView {
	id: string;
	name: string;
	label: string;
	type: string;
	/** The whole value, unit included — `1.5em`, not `1.5`. NOT `''` when unset: `getValue()` falls back
	 *  to the DEFAULT, which is why `borders.ts` gates every value it reads on `set`. */
	value: string;
	/** Carries a value ON THE SELECTED TARGET — what lights the label and enables the clear. */
	set: boolean;
	clearable: boolean;
	control: Control;
	property: PropertyLike;
	subs: PropertyView[];
}

export interface SectorView {
	id: string;
	label: string;
	open: boolean;
	properties: PropertyView[];
}

function optionsOf(property: PropertyLike): Choice[] {
	const raw = property.getOptions?.() ?? [];
	return raw.map((option) => {
		const value = property.getOptionId?.(option) ?? option.id;
		// `getOptionLabel` consults i18n; the raw fallbacks cover a fake and an unlocalised option.
		const label = property.getOptionLabel?.(value) ?? option.label ?? option.name ?? value;
		// GrapesJS's built-ins (display, flex-direction, background-repeat…) carry no label, so the
		// lookup answers the CSS keyword itself: `no-repeat` reads "No repeat" beside "Left", "Bold".
		return { value, label: label === value ? sentenceCase(value) : label };
	});
}

/** A sub-property that names one EDGE of a box: `margin-top`, `border-top-left-radius`. Matched on the
 *  CSS name, the way `borders.ts` matches its family, because a GrapesJS sub-property id is mangled
 *  (`padding-top-sub`) while the name it writes is not. */
const BOX_EDGE = /(?:^|-)(?:top|right|bottom|left)(?:-|$)/;

/** Whether a composite is a BOX — several numeric sub-properties, each naming an edge, so it gets the
 *  linked/split `BoxSides` treatment. `border-radius`'s four CORNERS qualify as well as `padding`'s
 *  sides, which is why `BoxSides` is parts-generic.
 *
 *  THE EDGE TEST IS THE POINT, not a refinement of it. `BoxSides` offers "one value for every side",
 *  which is only a coherent question when the parts ARE sides of one thing; on a composite that merely
 *  happens to hold four numbers — min/max width and height — linking them would set a width from a
 *  height and the split grid would label two of them with the wrong axis. All-numeric alone classified
 *  any such group as a box. */
export function isBoxComposite(subs: readonly PropertyLike[]): boolean {
	return (
		subs.length > 1 &&
		subs.every((sub) => sub.getType() === 'number' && BOX_EDGE.test(sub.getName()))
	);
}

/** The control for one property. An unrecognised type renders as plain text bound to its own value:
 *  rendering nothing would make a control silently VANISH on a preset upgrade. `read.test.ts` asserts
 *  every `PROPERTY_TYPES` member maps to one. */
export function controlFor(property: PropertyLike): Control {
	const type = property.getType();
	switch (type) {
		case 'color':
			return { kind: 'colour' };
		case 'number': {
			const min = property.getMin?.();
			return {
				kind: 'length',
				units: property.getUnits?.() ?? [],
				...(typeof min === 'number' ? { min } : {})
			};
		}
		case 'radio':
			return { kind: 'choice', options: optionsOf(property) };
		case 'select':
			return { kind: 'select', options: optionsOf(property) };
		case 'slider':
			return {
				kind: 'slider',
				min: property.getMin?.() ?? 0,
				max: property.getMax?.() ?? 1,
				step: property.getStep?.() ?? 0.01
			};
		case 'composite': {
			const subs = property.getProperties?.() ?? [];
			if (!isBoxComposite(subs)) return { kind: 'group' };
			const first = subs[0];
			const min = first?.getMin?.();
			return {
				kind: 'parts',
				parts: subs.map((sub) => ({ key: sub.getId(), label: sub.getLabel() })),
				// The first sub-property's units — the same for every part of a box by construction.
				units: first?.getUnits?.() ?? [],
				...(typeof min === 'number' ? { min } : {})
			};
		}
		// Noted above the labels because ESLint's `no-fallthrough` reports an empty case that carries a
		// comment. `file` (`background-image`) stays text DELIBERATELY — an upload tile would advertise
		// what almost no client honours; `stack` is pruned as unmailable; `base` is GrapesJS's default.
		case 'file':
		case 'stack':
		case 'base':
		default:
			return { kind: 'text' };
	}
}

/** British English at the VIEW only. Our per-side border composites label their colour sub-property
 *  `Colour`; the preset's own `border` above them keeps GrapesJS's `Color`, because renaming it in our
 *  definition collides with the `-sub` i18n keys. Whole-word, so `Colorimetry` is safe; keeps case. */
export function britishLabel(label: string): string {
	return label.replace(/\bColor\b/g, 'Colour').replace(/\bcolor\b/g, 'colour');
}

/** A bare number, so a unit is joined only to what needs one — never `solid`, a colour or `auto`. */
const BARE_NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

/** One property's value with its unit back on it. A numeric property answers the NUMBER alone; left
 *  apart, `UnitInput` falls back to the first unit it offers, so a `1.5em` padding shows as `1.5px`
 *  and the panel writes that back, converting the author's unit. Every control binds `view.value`. */
export function composedValue(property: PropertyLike): string {
	const value = String(property.getValue() ?? '').trim();
	const unit = (property.getUnit?.() ?? '').trim();
	return unit && BARE_NUMBER.test(value) ? `${value}${unit}` : value;
}

export function propertyView(property: PropertyLike): PropertyView {
	const control = controlFor(property);
	const subs =
		control.kind === 'parts' || control.kind === 'group'
			? (property.getProperties?.() ?? []).map(propertyView)
			: [];
	return {
		id: property.getId(),
		name: property.getName(),
		label: britishLabel(property.getLabel()),
		type: property.getType(),
		value: composedValue(property),
		// `noParent` asks "did the author set this HERE"; without it every inherited label would light.
		set: property.hasValue({ noParent: true }),
		clearable: property.canClear(),
		control,
		property,
		subs
	};
}

/** The whole rail. Both levels are filtered and the property level is not redundant:
 *  `Sector.getProperties()` does NOT filter (grapes.mjs :66720), so a partly-inapplicable sector would
 *  draw rows whose writes go nowhere. One that answers nothing is kept — `visible` defaults true. */
export function sectorViews(styleManager: StyleManagerLike | undefined): SectorView[] {
	const sectors = styleManager?.getSectors?.({ visible: true }) ?? [];
	return sectors.map((sector) => ({
		id: sector.getId(),
		label: sector.getName(),
		open: sector.isOpen(),
		properties: sector
			.getProperties()
			.filter((property) => property.isVisible?.() ?? true)
			.map(propertyView)
	}));
}

/** Whether a property carries a value anywhere in its tree. Not just `view.set`: `border-left` answers
 *  `hasValue` false while its Colour sub is set, so its heading stayed grey over a coloured border. */
export function propertySet(view: PropertyView): boolean {
	return view.set || view.subs.some(propertySet);
}

/** Whether a sector holds anything the author set — the dot on its collapsed header. */
export function sectorSet(sector: SectorView): boolean {
	return sector.properties.some(propertySet);
}

/** A box's part values by sub-property id, IN DECLARED ORDER — `BoxSides` reads them in key order. */
export function partValues(view: PropertyView): Record<string, string> {
	const values: Record<string, string> = {};
	for (const sub of view.subs) values[sub.id] = sub.value;
	return values;
}

/** Clearing goes through `clear()`, not `upValue('')` — an empty string is still a declaration, and
 *  only an absent one lets the element inherit in the export.
 *
 *  `partial` is GrapesJS's OWN word for a value still being chosen, and it is what keeps a gesture
 *  off the undo stack: the property model turns it into `avoidStore`, so the change applies to the
 *  canvas and is not recorded. Without it, dragging a colour pad wrote one undo entry PER POINTER
 *  MOVE — a hundred shades between picking up the handle and letting go, and Ctrl+Z had to walk back
 *  through every one of them to reach the colour before. The caller is responsible for one final
 *  write WITHOUT the flag, which is the entry the author actually undoes. */
export function write(property: PropertyLike, value: string, opts?: { partial?: boolean }): void {
	// The opts argument is passed only when there IS one. A one-argument call is the shape GrapesJS
	// itself uses, and handing it an explicit `undefined` is a different call — one a strict test
	// double reads as a second argument, and one a future destructure inside the library could too.
	if (value === '') {
		if (opts) property.clear(opts);
		else property.clear();
	} else if (opts) {
		property.upValue(value, opts);
	} else {
		property.upValue(value);
	}
}

/** Write a box composite's parts. SYNCHRONOUS, AND IT MUST STAY THAT WAY: backbone-undo fuses one
 *  callstack's actions under a single `magicFusionIndex`, so an `await` here leaves one Ctrl+Z undoing
 *  one side of four. Unchanged parts are skipped, so a uniform box puts no no-op on the stack. */
export function writeParts(
	view: PropertyView,
	values: Record<string, string>,
	opts?: { partial?: boolean }
): void {
	for (const sub of view.subs) {
		const next = values[sub.id];
		if (next === undefined || next === sub.value) continue;
		write(sub.property, next, opts);
	}
}
