/**
 * Shared GrapesJS authoring machinery.
 *
 * A SUBPATH export (`@graphene/ui/grapes`), not the package root, for the reason `book/listing` is:
 * these names are editor-internal and collision-prone — `read`, `write`, `PropertyView` — and the
 * root is imported by every page in three portals.
 *
 * WHY THIS IS NOT INSIDE AN EDITOR. GrapesJS renders its own style manager, and it is exactly the
 * native widget `docs/design-language.md` exists to keep out. Suppressing it (`styleManager:
 * { custom: true }`) leaves the MODELS intact and the VIEW ours, so the panel below is the shadcn
 * rail over GrapesJS's own state. Two editors now need that — the email templates one and the
 * document templates one — and the second must not reimplement it, because the tricky parts are not
 * visual: which composite owns a declaration, when a value has genuinely been SET rather than
 * defaulted, and which events mean the models have moved underneath you.
 *
 * ORIGIN, and how to reconcile it: the style panel and the four inspector primitives were written on
 * the email-templates branch (`feat/email-editor-ui`) and are copied here VERBATIM apart from
 * import paths, the border constants (now `css-borders.ts`, which the email editor keeps in its own
 * GrapesJS setup) and one test that scanned its caller. Copied rather than reimplemented so that
 * when that branch merges the reconciliation is a DELETION — point its editor at this module — and
 * never a diff between two drifted implementations of the same panel.
 */
export { default as StylePanel } from './style-panel/style-panel.svelte';
export { default as PropertyControl } from './style-panel/property-control.svelte';
export { default as BorderControl } from './style-panel/border-control.svelte';
export {
	type Choice,
	type PropertyLike,
	type PropertyView,
	type SectorLike,
	type SectorView,
	type StyleManagerLike,
	PROPERTY_TYPES,
	partValues,
	propertySet,
	sectorSet,
	sectorViews,
	write,
	writeParts
} from './style-panel/read.js';
export {
	type BorderAction,
	type BorderEdit,
	type BorderFamily,
	type BorderRole,
	type BorderState,
	BORDER_FAMILY,
	borderFamily,
	borderPlan,
	borderSub,
	borderWidthPresent,
	clearBorderPart,
	held,
	isBorderFamily,
	panelRows,
	readBorderState
} from './style-panel/borders.js';
export {
	type BorderSide,
	BORDER_ANCHOR,
	BORDER_SIDES,
	BORDER_STYLE_DEFAULT,
	BORDER_WIDTH_DEFAULT,
	borderSideProperty
} from './style-panel/css-borders.js';

// ---- code view -------------------------------------------------------------------------------
// A read-only `</>` pane over the same document the canvas holds, with folding, formatting and
// Jinja-aware colouring, and a hold-to-confirm unlock where the caller can take a hand edit back.
//
// Generalised from the email branch's copy in two places, both because a second editor now mounts
// it: the two SHAPES take their labels and their explanatory sentences from the caller (a document
// has no "Sent" shape and an email has no "Template" one), and the attributes coloured as the
// editor's OWN bookkeeping are passed in rather than read from one editor's condition vocabulary.
// `html-scan.ts` is the three tag-scanning helpers the highlighter needed, lifted out of that
// editor's `conditions/compile.ts` — they are about HTML, not about conditions.
export { default as CodeView, type CodeShape } from './code-view/code-view.svelte';
export { CODE_CLASSES, highlightHtml, type HighlightSeg } from './code-view/highlight.js';
export { formatHtml } from './code-view/format.js';
export { foldRanges, hiddenLines, rangeAt } from './code-view/folds.js';
export { findCloseTag, readAttr, readOpenTag, type OpenTag } from './code-view/html-scan.js';
export { jinjaRuns, type JinjaRun } from './jinja-highlight.js';
