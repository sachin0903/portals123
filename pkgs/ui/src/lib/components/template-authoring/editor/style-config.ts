/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Style-manager sector configuration — replaces GrapesJS's default sectors
 * (General/Dimension/Typography/Decorations/Extra) with the prototype's curated
 * set: Layout · Size & Space · Position · Typography · Background · Borders ·
 * Effects. Most properties reference GrapesJS's built-in definitions by name;
 * font-weight / font-style / text-align are declared as radio button-rows.
 */

// AN OPTION'S LABEL IS A WORD, NEVER MARKUP. These two rows used to carry an inline `<svg>` string
// as their `label`, which worked only while GrapesJS drew the rail: our own panel takes the label as
// TEXT, so `text-align` announced itself to a screen reader as `<svg viewBox="0 0 24 24"…` and
// `float` — which has no entry in `CHOICE_ICONS` — rendered the raw markup as three unreadable
// dropdown options. The PICTURES live in `property-control.svelte`, keyed by property, which is the
// only place that knows whether a control has the room to be icon-only.

export const STYLE_SECTORS: any[] = [
	{
		name: 'Layout',
		open: false,
		properties: [
			'display',
			'flex-direction',
			'justify-content',
			'align-items',
			'flex-wrap',
			// `gap` isn't a GrapesJS built-in, so a bare string gets the default number config
			// (units: [], unit: ''), producing an invalid unitless `gap: 20` that the browser
			// silently drops. Give it explicit length units (px default) like margin/padding.
			{
				property: 'gap',
				type: 'number',
				units: ['px', 'rem', 'em', '%'],
				min: 0,
				default: '0'
			},
			// NOT AN ALIGNMENT CONTROL, and "Text wrap" read as one. This is `float`: None keeps the
			// component in normal flow, Left and Right take it out of the flow so surrounding content
			// wraps around it (the classic "wrap text around an image"). There is no `float: center` —
			// centring a block is a margin, not a float — so a Centre option here could only be a
			// choice that silently did nothing. Alignment WITH a centre is `text-align`, one section
			// down under Typography, which is where it belongs and where it already works.
			{
				property: 'float',
				name: 'Wrap around',
				type: 'radio',
				default: 'none',
				options: [
					{ id: 'none', label: 'None' },
					{ id: 'left', label: 'Left' },
					{ id: 'right', label: 'Right' }
				]
			}
		]
	},
	{
		// Open by default — width/margin/padding are among the first things an author reaches for,
		// so the panel shows controls on select rather than a wall of collapsed accordions.
		name: 'Size & Space',
		open: true,
		properties: ['width', 'height', 'margin', 'padding']
	},
	{
		name: 'Position',
		open: false,
		properties: ['position', 'top', 'right', 'bottom', 'left', 'z-index']
	},
	{
		// Open by default — font / size / colour / alignment are the most-used controls for a
		// document template, so they're visible immediately instead of behind a collapsed sector.
		name: 'Typography',
		open: true,
		properties: [
			'font-family',
			'font-size',
			{
				property: 'font-weight',
				type: 'radio',
				default: '400',
				options: [
					{ id: '300', label: 'Light' },
					{ id: '400', label: 'Normal' },
					{ id: '600', label: 'Semi' },
					{ id: '700', label: 'Bold' }
				]
			},
			{
				property: 'font-style',
				type: 'radio',
				default: 'normal',
				options: [
					{ id: 'normal', label: 'Normal' },
					{ id: 'italic', label: 'Italic' }
				]
			},
			{
				property: 'text-decoration',
				type: 'radio',
				default: 'none',
				options: [
					{ id: 'none', label: 'None' },
					{ id: 'underline', label: 'Underline' },
					{ id: 'line-through', label: 'Strike' }
				]
			},
			{
				property: 'text-align',
				type: 'radio',
				default: 'left',
				options: [
					{ id: 'left', label: 'Left' },
					{ id: 'center', label: 'Centre' },
					{ id: 'right', label: 'Right' },
					{ id: 'justify', label: 'Justify' }
				]
			},
			// Unitless by default: line-height is a multiplier (1.5), not a length. The
			// built-in def forces px, turning a typed `2` into a broken `2px`. Listing ''
			// first makes unitless the default unit while still allowing explicit px/em/%.
			{
				property: 'line-height',
				type: 'number',
				units: ['', 'px', 'em', 'rem', '%'],
				default: ''
			},
			'letter-spacing',
			'color'
		]
	},
	{
		name: 'Background',
		open: false,
		properties: ['background-color', 'background-image', 'background-repeat', 'background-size']
	},
	{
		name: 'Borders',
		open: false,
		properties: ['border', 'border-radius']
	},
	{
		// NO BOX-SHADOW. WeasyPrint has no shadow property at all — not unimplemented for some value,
		// absent from its property table entirely (69.0: nothing matching `shadow` in INITIAL_VALUES).
		// So a shadow set here rendered in the canvas, which is a browser, and vanished from the PDF,
		// which is not: the author sees it work and the client never receives it. `opacity` is real
		// and stays. This is the chart-typography defect in another sector — a control that cannot
		// change the artefact it claims to style.
		name: 'Effects',
		open: false,
		properties: ['opacity']
	}
];

// ── Per-type sector gating ────────────────────────────────────────────────────
// GrapesJS auto-hides a whole sector when every one of its properties is hidden for
// the selected component (StyleManager sets `sector.visible = props.some(isVisible)`),
// and a property is hidden when its CSS name is in the component's `unstylable`. So a
// type "shows only sectors X/Y" simply by listing every OTHER sector's properties in
// `unstylable` — no bespoke sector-visibility mechanism required. These helpers build
// those lists from STYLE_SECTORS above, so they can never drift from the real sectors.

/** Every CSS property a sector entry owns. A COMPOSITE contributes its own name AND its members':
 *  `unstylable` is matched per property, so a composite that listed only its own name would hide the
 *  disclosure while leaving each member stylable through any other door — and a sector is auto-hidden
 *  only when EVERY one of its properties is hidden, so a half-listed composite keeps an empty sector
 *  on screen. No sector declares a composite today; this is what keeps the next one honest. */
const propNames = (p: any): string[] =>
	typeof p === 'string' ? [p] : [p.property, ...((p.properties as any[]) ?? []).flatMap(propNames)];

/** Short sector key → the CSS property names it owns (derived from STYLE_SECTORS). */
export const SECTOR_PROPS: Record<string, string[]> = (() => {
	const keyByName: Record<string, string> = {
		Layout: 'layout',
		'Size & Space': 'size',
		Position: 'position',
		Typography: 'typography',
		Background: 'background',
		Borders: 'borders',
		Effects: 'effects'
	};
	const out: Record<string, string[]> = {};
	for (const sector of STYLE_SECTORS) {
		const key = keyByName[sector.name] ?? String(sector.name).toLowerCase();
		out[key] = (sector.properties as any[]).flatMap(propNames);
	}
	return out;
})();

const ALL_SECTOR_KEYS = Object.keys(SECTOR_PROPS);

/** `unstylable` list that HIDES the named sectors (all others stay visible). */
export function hideSectors(...keys: string[]): string[] {
	return keys.flatMap((k) => SECTOR_PROPS[k] ?? []);
}

/** `unstylable` list that shows ONLY the named sectors (hides every other). */
export function showOnlySectors(...keys: string[]): string[] {
	return ALL_SECTOR_KEYS.filter((k) => !keys.includes(k)).flatMap((k) => SECTOR_PROPS[k] ?? []);
}

/** `unstylable` fragment that hides every property of a sector EXCEPT the kept ones — for exposing
    just a couple of controls from a sector (e.g. Layout: keep display + text-wrap, hide the flex
    arrangement props that would rearrange a component's own contents). */
export function sectorPropsExcept(key: string, ...keep: string[]): string[] {
	return (SECTOR_PROPS[key] ?? []).filter((p) => !keep.includes(p));
}
