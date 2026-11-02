/**
 * The attributes THIS editor writes onto the document, so the code view can colour them.
 *
 * The rule is deliberately mechanical: an attribute in the `data-` namespace that the editor
 * WRITES is its own bookkeeping, and dropping one in a hand edit silently costs whatever it
 * carried — a condition stops being editable from the panel, a chart loses its data source, a
 * loop stops being a loop. An attribute the AUTHOR typed is not in here and is left alone.
 *
 * Mechanical because the alternative is taste: someone deciding `data-cols` is "just structure"
 * while `data-loop-key` is "real", and being wrong in a way nothing catches until an author
 * deletes one. `marker-attrs.test.ts` scans the editor's own source for `data-*` literals and
 * fails when this list and the code disagree, in either direction — so a new attribute cannot be
 * added without deciding it belongs here, and a retired one cannot linger.
 */
export const EDITOR_ATTRS: readonly string[] = [
	'data-accent',
	'data-branch',
	'data-branch-label',
	'data-cols',
	'data-columns',
	'data-comp-colors',
	'data-comp-columns',
	'data-comp-cond',
	'data-comp-cond-config',
	'data-comp-data-source',
	'data-comp-dataset-uid',
	// Per-type shape: how a line is stroked and whether it carries points or a filled area, how wide
	// a bar is and whether its corners are rounded, how thick a donut's ring is. Blank ⇒ the drawing
	// these charts have always made (`charts.py`, `_LINE_DASH` / `_BAR_FILL` / `_RING_HOLE`).
	'data-comp-line-style',
	'data-comp-show-points',
	'data-comp-area-fill',
	'data-comp-bar-width',
	'data-comp-bar-corners',
	'data-comp-ring-width',
	// The gridlines and the axis lines, as one colour. Blank ⇒ the default pair.
	'data-comp-grid-color',
	// The chart's own background paint — moved off the frame's style rail, where it was two panels
	// from the chart it applied to. Blank ⇒ transparent (`charts.py::_card_bg`).
	'data-comp-background',
	// Chart typography — the font stack, the base size every other size scales from, and the
	// text colour. Blank means the renderer's own default (`charts.py::_typography`).
	'data-comp-font-family',
	'data-comp-font-size',
	'data-comp-text-color',
	'data-comp-header-bg',
	'data-comp-header-fg',
	'data-comp-headers',
	'data-comp-id',
	'data-comp-inline-data',
	'data-comp-label-field',
	'data-comp-line-width',
	// How a figure on the chart reads, and whether each one is printed on it.
	// Per-column width and alignment for a data table.
	'data-comp-widths',
	'data-comp-aligns',
	'data-comp-value-format',
	'data-comp-value-decimals',
	'data-comp-show-values',
	'data-comp-show-grid',
	'data-comp-show-legend',
	'data-comp-source-mode',
	'data-comp-striped',
	'data-comp-title',
	'data-comp-type',
	'data-comp-value-field',
	'data-comp-x-field',
	'data-comp-x-label',
	'data-comp-y-field',
	'data-comp-y-label',
	'data-cond-field',
	'data-cond-mode',
	'data-cond-op',
	'data-cond-value',
	'data-condition',
	'data-condition-action',
	'data-dataset',
	'data-editor-label',
	'data-gjs-type',
	'data-has-else',
	'data-id',
	'data-item-var',
	'data-jdesign',
	'data-jinja-key',
	'data-layout',
	'data-loop-key',
	'data-rows',
	'data-template',
	'data-tmpl-ph',
	'data-v',
	'data-variant'
];
