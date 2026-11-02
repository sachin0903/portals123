/* eslint-disable @typescript-eslint/no-explicit-any */
// Email-editor factory: dynamic-imports GrapesJS + the newsletter preset (both touch `window`, so browser-only), inits, registers OUR palette, mounts the managers into our own rails, loads the content. The preset is used for its email-tuned style sectors and CSS-inline command only (`blocks: []`), and its panel bars are dropped after init because the chrome is ours.

import {
	BRAND_ARM_ATTR,
	BRAND_ARM_LOGO,
	encodeFallback,
	FALLBACK_ATTR,
	FIELD_ATTR,
	FIELD_FALLBACK_ATTR,
	FIELD_FILTER_ATTR,
	visibilityAttributes
} from './conditions/attrs.js';
import { fieldToken, parseFieldToken } from './field-filters.js';
import { BRAND_LOGO_FIELD, labelForField } from './merge-fields.js';

// Email palette, shared by the starter skeleton and every palette block so the two cannot drift block by block. Tracks the shipped inks in `apps/api/app/email/shell.py::shell_html`: heading, body copy, secondary copy (sign-offs, captions), small print (footers), hairline rule, call-to-action fill.
const FONT = 'Arial,Helvetica,sans-serif';
// THE TYPE SCALE IS THREE STEPS — 22px heading, 15px body, 13px small print. One family, with
// hierarchy carried by size, weight and ink rather than by a second face or a fourth size.
const INK = '#0f172a';
const BODY_INK = '#334155';
// Secondary copy AND every piece of small print. There is no FAINT_INK any more: it was
// '#94a3b8', 2.56:1 on white — under half the 4.5:1 WCAG AA asks of body text — and it carried
// the footer and the support address. MUTED_INK is 4.76:1. See `shell.py` for the full note.
const MUTED_INK = '#64748b';
// The small-print step, shared by the masthead eyebrow and the footer. 13px, not 12: below 13
// text stops being reliably readable on a phone.
const SMALL_SIZE = '13px';
const RULE = '#e2e8f0';
// No page-background constant: the sheet IS the page (`PAGE_HTML`) and its colour is the author's.
const CTA_BG = '#0f172a';

// THE BULLETPROOF BUTTON. The CELL carries the padding, fill and radius, the ANCHOR typography and ink only: Word gives an `<a>` no `display:inline-block`, so padding on one shrinks to the text box, and its Hyperlink character style beats a plain inline `color`. The hit area is therefore the label, which is why `display` is absent rather than left at `inline-block`; no `bgcolor` beside the CSS fill either, Word honouring `background-color` on a `<td>`. `app/email/shell.py::cta_block` is a third copy, in Python, and a cross-language test pins all three ink layers on both sides.
// THE INK IS THREE LAYERS, AND AN INLINE `!important` IS NOT ONE: Word INVALIDATES the declaration carrying it (hteumeuleu/email-bugs#31), so a flagged `color:#ffffff` renders BLACK, the fill's own colour. (1) the label span's unflagged `color`, the load-bearing layer, Word styling the `<a>` ELEMENT and not a child; (2) the anchor's own unflagged `color`, for label text the RTE has left outside the span; (3) `app/email/document.py::MSO_CTA_INK`, a conditional STYLESHEET, the one place Word does honour the flag — hence `class="cta"`. Layer 3 must not target the span: `color` inherits, and an inherited value never competes, so a firm's own label colour still wins. `syncButtonLabelInk` keeps (1) and (2) in step both ways, and the span is NAMED so the select-parent chain reads as one object.
// Round trip verified in grapes.mjs: `parseStyle` (:26593) keeps a value verbatim, `styleToString` (:27032) writes it back, `updateStyle` (:35007-35018) sets the whole `style` attribute, and `disableTextInnerChilds` defaults false (:32949), so the span stays selectable. A VML rectangle stays rejected: it needs `xmlns:v`/`xmlns:o` on `<html>` and is uneditable in this rail.

const BUTTON_CELL = `padding:12px 24px;background-color:${CTA_BG};border-radius:6px`;
const BUTTON_LINK = `font-family:${FONT};font-size:15px;font-weight:600;line-height:1.2;color:#ffffff;text-decoration:none`;
const BUTTON_LABEL = `color:#ffffff`;
// The hook `app/email/document.py::MSO_CTA_INK` targets. Styled nowhere in the canvas, so a duplicated button cannot render naked — the failure the preset's own `<a class="button">` shipped.
const BUTTON_CLASS = 'cta';
// Written out at both call sites rather than built by a helper: `test_email_publish_guards.py` reads these literals as TEXT, and a `${helper(...)}` would hide the label from it.

// The skeleton offered on a blank canvas. It is CONTENT, not a shell — `PAGE_HTML` already ships the height and inset, so a band-and-card here would paint a second card inside the sheet. It obeys the `background-color`-not-`background` rule for the reason `UNMAILABLE_STYLE_PROPERTIES` gives, and the test pinning that rule reads this constant too.
export const STARTER_HTML = `
<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
  <tr><td style="font-family:${FONT};">
    <h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;color:${INK};">Hello {{ first_name }},</h1>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:${BODY_INK};">Write your message here. Use the <strong>Insert field</strong> menu to add personalised details, and <strong>Preview</strong> to see it with sample values.</p>
    <table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0;"><tr><td style="${BUTTON_CELL};">
      <a class="${BUTTON_CLASS}" href="#" style="${BUTTON_LINK};"><span style="${BUTTON_LABEL}">Open portal</span></a>
    </td></tr></table>
    <p style="margin:16px 0 0;font-size:15px;line-height:1.6;color:${MUTED_INK};">Best regards,<br />{{ brand.name }}</p>
  </td></tr>
</table>`.trim();

// Block palette, reconciled with the document rail (templates/editor/blocks.ts): same `icon()` vocabulary (a 24x14 viewBox drawn 28x18), same grouping, same ids where the concept is shared. The preset's own blocks are NOT used (`blocks: []`): 24x24 glyphs that letterbox, its own naming, and three of them reference classes it never defines a rule for, so they land visibly unstyled.
// THREE MARKUP RULES, each easy to break. (1) Inline-styled, table-based, no modern CSS: `avoidInlineStyle: false` + `forceClass: false` keep styling on the element, so a class-styled block would render naked once duplicated, and Outlook is the floor — explicit presentation-table attributes, no flexbox or grid, no 8-digit hex alpha. (2) No whitespace between tags: GrapesJS promotes an element whose children include a raw textnode to a single `text` component, so a prettily-indented section collapses into one RTE field and stops being a droppable structure. (3) Every selectable element carries `data-gjs-name` — see NAMES.

// NAMES. The Style rail heads itself with the selected component's name, and GrapesJS's own answer is an internal type id — a Button being a `<table><tr><td><a>` reports Table / Table row / Table cell / Link, so an author is told they selected a Link and that its fill is a Table cell.
// `data-gjs-name` is safe both ways: `getName()` resolves `custom-name -> i18n[name] -> name -> i18n[tagName] -> tagName -> i18n[type] -> type` and the attribute lands in `name`, above both fallbacks; and it never reaches the recipient, `splitPropsFromAttr` lifting every `data-gjs-*` into a model prop while `toHTML()` writes them back only under `withProps`, which nothing here passes (verified by probe as well as in the parser source). Two rules follow: a name is a Capitalised human word and never a bare GrapesJS key, since `i18n[name]` is consulted BEFORE `name` so a block naming itself `cell` resolves straight back to 'Table cell' (pinned by a test); and a name describes the RELATIONSHIP — 'Button fill', 'Button link' — so the tree reads as one object.
// TWO LAYERS, BECAUSE THE ATTRIBUTE IS SESSION-SCOPED: being consumed is what keeps it out of the inbox, and it also means a REOPENED template is re-parsed from markup carrying none of them. Hence the per-block name, plus `i18n.domComponents.names` in `grapesjs.init` as the generic fallback the chain ends at. The implied `<tbody>` DOMParser inserts is covered by the second layer only.

// The FALLBACK component vocabulary — GrapesJS's own `domComponents.names` in an email author's words, and the end of the `getName()` chain, so it answers for a REOPENED template whose markup carries no `data-gjs-name`. Keys are GrapesJS's. A layout table is a 'Section' because in email it is not tabular data, it is the only way to lay anything out.
export const COMPONENT_NAMES: Record<string, string> = {
	'': 'Element',
	wrapper: 'Email',
	table: 'Section',
	tbody: 'Section body',
	thead: 'Section head',
	tfoot: 'Section foot',
	row: 'Row',
	cell: 'Cell',
	text: 'Text',
	link: 'Link',
	image: 'Image',
	label: 'Label',
	video: 'Video',
	map: 'Map',
	comment: 'Comment'
};

/** A palette glyph in the document rail's idiom: a 24×14 box drawn at 28×18 in currentColor. */
const icon = (inner: string): string =>
	`<svg viewBox="0 0 24 14" fill="none" xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;

// A full-width presentation table — email's block-level `<div>`. The attributes are not decoration: Outlook adds its own cell spacing unless they are zeroed, and `role` keeps layout tables out of the screen-reader table vocabulary.
const tbl = (name: string, rows: string, style = ''): string =>
	`<table data-gjs-name="${name}" role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;${style}">${rows}</table>`;

const row = (name: string, cells: string): string => `<tr data-gjs-name="${name}">${cells}</tr>`;

// An empty structural cell, hittable before it has content. `height` on a `<td>` behaves as a minimum in every engine — the table counterpart of `min-height` on the document rail's empty columns.
const slot = (name: string, style: string): string =>
	`<td data-gjs-name="${name}" style="height:48px;vertical-align:top;${style}"></td>`;

/** Body copy, restated on every text cell: Outlook resets the font inside tables. */
const COPY = `font-family:${FONT};font-size:15px;line-height:1.6;color:${BODY_INK}`;

const BASIC = 'Basic';
const MEDIA = 'Media';
const MESSAGE = 'Message';

// THE MASTHEAD IS ONE IMAGE WITH A FALLBACK. The second outcome — the firm's name as type — is a PROPERTY of that image, held in `FALLBACK_ATTR` and expanded by the compile step, so no `{% if %}` reaches the canvas for an author to see, be unable to edit and half-break. `brand.logo_url` is the RESOLVED masthead (`email.py::masthead_image`), so this one `<img>` covers every firm with any brand imagery. `data-brand-arm="logo"` lets the canvas preview which outcome applies; it is inert at send.
const LOGO_ARM_STYLE = 'height:28px;max-width:220px;margin:0 0 28px;display:block';
// The fallback's own look, mirroring `app/email/shell.py::text_wordmark` / `WORDMARK_STYLE`. It travels INSIDE the fallback attribute, so the compile step never has to know what a wordmark looks like, and its `font-family` is not optional: nothing above this div sets one in a real client, so without it the firm's name shipped in the client's default serif while the canvas, which supplies a stack, looked right.
const WORDMARK_ARM_STYLE =
	`margin:0 0 26px;font-family:${FONT};font-size:${SMALL_SIZE};font-weight:700;` +
	`letter-spacing:0.09em;line-height:1.4;text-transform:uppercase;color:${MUTED_INK}`;

// `show when <field> is set` / `is unset`, as the attributes the Visibility panel writes. Every block whose shipped form needs a CONDITION builds it through here, so the panel reads it back with `decodeVisibility` rather than an author facing hand-written Jinja they cannot edit. The footer's support address is the one consumer — a genuine either-or with nothing to be a property of.
function showWhen(field: string, op: 'set' | 'unset'): string {
	const attrs = visibilityAttributes({ action: 'show', match: 'all', rules: [{ field, op }] });
	return Object.entries(attrs)
		.filter(([, value]) => value !== null)
		.map(([name, value]) => ` ${name}="${escapeAttr(String(value))}"`)
		.join('');
}

export const BRAND_LOGO_FALLBACK = encodeFallback({
	field: BRAND_LOGO_FIELD,
	text: '{{ brand.name }}',
	tag: 'div',
	style: WORDMARK_ARM_STYLE
});

export const BRAND_LOGO_HTML =
	`<img data-gjs-name="Brand logo" ${BRAND_ARM_ATTR}="${BRAND_ARM_LOGO}" ` +
	`${FALLBACK_ATTR}="${escapeAttr(BRAND_LOGO_FALLBACK)}" ` +
	`src="{{ ${BRAND_LOGO_FIELD} }}" alt="{{ brand.name }}" height="28" style="${LOGO_ARM_STYLE}">`;

// THERE IS ONE BRAND BLOCK, and for a while there were two. A second block placed
// `brand.icon_url` — the firm's square mark — beside this one placing `brand.logo_url`, which read
// as two components for one idea and made an author choose between them with nothing to go on.
// It is not a choice they should have to make: **`brand.logo_url` IS the resolved masthead**
// (`app/email/transport.py::_email_asset_urls` — `(icon or wordmark)` when the firm's own
// `logo_variant` says `icon`, `(wordmark or icon)` otherwise), so a firm that uploaded only a mark,
// or that asked for the mark treatment, already gets its mark THROUGH THIS BLOCK. Placing
// `icon_url` directly was therefore not a second capability, it was a second way to spell the same
// one that ignored the firm's stated treatment.
//
// The mark is still reachable where it is genuinely a different question: as a CONDITION
// (`brand.icon_url` in `merge-fields.ts` — "is there a square mark") and as an Image's source.
// What is gone is the second entry in the palette. `brand-block.test.ts` fails if a second appears.

// A one-cell full-width table: the band a body-level drop is wrapped in (see WHERE A DROP LANDS). No palette block ships it — the multi-column group is deliberately absent — but the editor builds it.
export const SECTION_HTML = tbl(
	'Full Width',
	row('Full Width row', slot('Full Width cell', 'padding:0')),
	'margin:0 0 12px'
);

interface EmailBlockDef {
	id: string;
	label: string;
	category: string;
	media: string;
	content: string | Record<string, unknown>;
	activate?: boolean;
	/** Select the placed block without ACTIVATING it — `appendBlockOnClick` honours both, and activate
	 *  is what opens a component's own editing affordance (for an image, the asset manager we do not
	 *  render). */
	select?: boolean;
}

// The palette in rail order: content, then layout, then domain furniture — the document rail's convention. Exported so the set is pinnable without an editor instance.
export const EMAIL_BLOCKS: readonly EmailBlockDef[] = [
	{
		id: 'text',
		label: 'Text',
		category: BASIC,
		activate: true,
		media: icon(
			'<rect x="1" y="2" width="22" height="2" rx=".75" fill="currentColor"/><rect x="1" y="6" width="22" height="2" rx=".75" fill="currentColor" opacity=".55"/><rect x="1" y="10" width="15" height="2" rx=".75" fill="currentColor" opacity=".4"/>'
		),
		content: `<p data-gjs-type="text" data-gjs-name="Text" style="margin:0 0 16px;${COPY}">Write your message here.</p>`
	},
	{
		id: 'heading',
		label: 'Heading',
		category: BASIC,
		activate: true,
		media: icon(
			'<rect x="1" y="1" width="13" height="4" rx="1" fill="currentColor"/><rect x="1" y="7.5" width="22" height="2" rx=".75" fill="currentColor" opacity=".45"/><rect x="1" y="11" width="17" height="2" rx=".75" fill="currentColor" opacity=".3"/>'
		),
		// h2, not h1: the starter skeleton owns the one h1 (the greeting), so a mid-email heading is a section.
		content: `<h2 data-gjs-type="text" data-gjs-name="Heading" style="margin:0 0 12px;font-family:${FONT};font-size:20px;line-height:1.3;font-weight:700;color:${INK}">Heading</h2>`
	},
	{
		id: 'typo-bullet-list',
		label: 'Bullet list',
		category: BASIC,
		media: icon(
			'<circle cx="2.5" cy="3.5" r="1.5" fill="currentColor"/><rect x="6" y="2.5" width="16" height="2" rx=".75" fill="currentColor"/><circle cx="2.5" cy="8" r="1.5" fill="currentColor"/><rect x="6" y="7" width="13" height="2" rx=".75" fill="currentColor"/>'
		),
		// The document version's `display:inline-block` (a WeasyPrint quirk) is dropped: an inline-block list is a rendering lottery in Outlook.
		content: `<ul data-gjs-name="Bullet List" style="margin:0 0 16px;padding-left:22px;${COPY}"><li data-gjs-name="List item" style="margin-bottom:6px">First item</li><li data-gjs-name="List item" style="margin-bottom:6px">Second item</li><li data-gjs-name="List item" style="margin-bottom:6px">Third item</li></ul>`
	},
	{
		id: 'typo-numbered-list',
		label: 'Numbered list',
		category: BASIC,
		media: icon(
			'<rect x="1" y="1.5" width="3" height="3" rx=".5" fill="currentColor" opacity=".7"/><rect x="6" y="2.5" width="16" height="2" rx=".75" fill="currentColor"/><rect x="1" y="6.5" width="3" height="3" rx=".5" fill="currentColor" opacity=".7"/><rect x="6" y="7.5" width="13" height="2" rx=".75" fill="currentColor"/>'
		),
		content: `<ol data-gjs-name="Numbered List" style="margin:0 0 16px;padding-left:22px;${COPY}"><li data-gjs-name="List item" style="margin-bottom:6px">First item</li><li data-gjs-name="List item" style="margin-bottom:6px">Second item</li><li data-gjs-name="List item" style="margin-bottom:6px">Third item</li></ol>`
	},
	{
		id: 'button',
		label: 'Button',
		category: BASIC,
		media: icon(
			'<rect x="3.5" y="2" width="17" height="10" rx="5" fill="currentColor" opacity=".16"/><rect x="3.5" y="2" width="17" height="10" rx="5" stroke="currentColor" stroke-width="1.2"/><rect x="8.5" y="6" width="7" height="2" rx="1" fill="currentColor"/>'
		),
		// The bulletproof button — padding, fill and radius on the CELL, nothing but typography on the anchor, sharing BUTTON_CELL / BUTTON_LINK with the starter CTA. See THE BULLETPROOF BUTTON. Not a `tbl()`: this table shrinks to its content, and it carries no `width` attribute at any level, an absence a test pins because adding one is the tempting way to build a full-width button and would reintroduce the width divergence. All FIVE levels are named, and the split is literal: the cell is the coloured body and the padding that sizes it, the anchor the URL and fallback ink, the span the words and the ink that reaches Outlook.
		content: `<table data-gjs-name="Button" role="presentation" border="0" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:24px 0"><tr data-gjs-name="Button row"><td data-gjs-name="Button fill" style="${BUTTON_CELL}"><a data-gjs-name="Button link" class="${BUTTON_CLASS}" href="#" style="${BUTTON_LINK}"><span data-gjs-name="Button label" style="${BUTTON_LABEL}">Open portal</span></a></td></tr></table>`
	},
	{
		id: 'link',
		label: 'Link',
		category: BASIC,
		media: icon(
			'<path d="M10.5 4H7.5a3 3 0 0 0 0 6h3M13.5 4h3a3 3 0 0 1 0 6h-3M9 7h6" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/>'
		),
		// Underlined AND coloured: several clients strip link colour but keep decoration, and Outlook re-colours undecorated links unpredictably.
		content: `<a data-gjs-name="Link" href="#" style="font-family:${FONT};font-size:15px;line-height:1.6;color:#1d4ed8;text-decoration:underline">Link text</a>`
	},
	{
		// THE BRAND LOGO, as one block rather than a hand-typed conditional. A masthead chooses at MERGE time between the firm's logo and its name and baking either arm loses one of them, so the choice is real — carried as the image's FALLBACK (see `BRAND_LOGO_HTML`) so no `{% if %}` reaches the canvas. Mirrors `app/email/shell.py::logo_wordmark` / `text_wordmark`, and the mirror is PINNED ONE SIDE AT A TIME, never across the two languages: `conditions/brand-logo.test.ts` asserts the styles THIS side emits as literals, `apps/api/tests/test_email/defaults.py` asserts the Python fallback against `email.WORDMARK_STYLE`, and nothing compares the two — so a change to either arm alone passes both suites and the two mastheads part company silently.
		id: 'brand-logo',
		label: 'Brand logo',
		category: MEDIA,
		media: icon(
			'<rect x=".7" y="3" width="9" height="8" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3 9l2-2.2L7.3 9" stroke="currentColor" stroke-width="1.1" fill="none" stroke-linejoin="round"/><rect x="12.5" y="4.6" width="10.8" height="1.7" rx=".85" fill="currentColor"/><rect x="12.5" y="8" width="7.5" height="1.7" rx=".85" fill="currentColor" opacity=".5"/>'
		),
		content: BRAND_LOGO_HTML
	},
	{
		id: 'image',
		label: 'Image',
		category: MEDIA,
		// SELECTED, NOT ACTIVATED. `activate` makes GrapesJS's image view open its asset manager, which
		// this editor deliberately does not render (`assetManager: { custom: true }`) — so placing an
		// image flashed a modal that closed itself and appeared to do nothing. Selecting it instead puts
		// the style rail on the Image sector, which is where the source, the firm's library and the
		// upload actually live.
		select: true,
		media: icon(
			'<rect x=".7" y=".7" width="22.6" height="12.6" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="6.5" cy="4.5" r="1.5" fill="currentColor"/><path d="M2 13l6-5 3.5 3 3-2.5L22 13" stroke="currentColor" stroke-width="1.2" fill="none" stroke-linejoin="round"/>'
		),
		// GrapesJS's own image type, so the drop opens the asset manager. `display:block` kills the baseline gap under an image in a cell, `border:0` the ring Outlook draws on linked images, `max-width` keeps it inside a narrow column. `name` is the object-form `data-gjs-name`, stated so the palette stays the single source of the name.
		content: {
			type: 'image',
			name: 'Image',
			style: { display: 'block', 'max-width': '100%', border: '0', 'line-height': '1' },
			// `src: ''` EXPLICITLY: GrapesJS's image type defaults it to a raw `<svg …>` placeholder
			// string, which reached the Source field as markup and would publish as `src="<svg …>"`.
			src: '',
			attributes: { alt: '' }
		}
	},
	{
		id: 'typo-divider',
		label: 'Divider',
		category: BASIC,
		media: icon(
			'<rect x="0" y="4.5" width="24" height="1" rx=".5" fill="currentColor" opacity=".3"/><rect x="0" y="7.5" width="24" height="1.5" rx=".75" fill="currentColor" opacity=".7"/><rect x="0" y="11" width="24" height="1" rx=".5" fill="currentColor" opacity=".3"/>'
		),
		// A bordered cell, not an `<hr>` and not a 1px background div: a top border on a collapsed cell is the one divider every client agrees on, and the `&nbsp;` at font-size 0 stops Gmail dropping the cell as empty without adding height.
		content: tbl(
			'Divider',
			row(
				'Divider row',
				`<td data-gjs-name="Divider rule" style="height:1px;padding:0;font-size:0;line-height:1px;border-top:1px solid ${RULE}">&nbsp;</td>`
			),
			'margin:20px 0'
		)
	},
	// NOTE — no Spacer block, and a test pins the absence. Vertical space is padding on the adjacent cell, and `height` stays in the rail, so a standalone gap is still hand-buildable from a Full Width row.
	{
		id: 'container-note-box',
		label: 'Note box',
		category: BASIC,
		media: icon(
			'<rect x="1" y="1" width="2.5" height="12" rx=".75" fill="currentColor"/><rect x="5" y="2" width="18" height="2.5" rx=".75" fill="currentColor" opacity=".8"/><rect x="5" y="6.5" width="15" height="1.5" rx=".5" fill="currentColor" opacity=".4"/>'
		),
		// The document `doc-callout` note variant as a table cell, its 8-digit-hex tints pre-mixed opaque because email clients do not parse them. The ONE callout, a test pinning the absence of a Highlight Box: Note Box is the shape the send path already has, the adviser note `{{ message }}` renders.
		content: tbl(
			'Note Box',
			row(
				'Note Box row',
				`<td data-gjs-name="Note Box panel" style="padding:12px 14px;background-color:#f3f7fe;border-left:3px solid #3b82f6;font-family:${FONT};font-size:15px;line-height:1.6;color:${BODY_INK}">Add note content here.</td>`
			),
			'margin:12px 0'
		)
	},

	{
		id: 'sec-container',
		// 'Email card', NOT 'Container': this is not a box you put things in, it is the WHOLE EMAIL's shell, a full-bleed wrapper around a centred 600px card — read as "a container for my content" and dropped into a started email it gives a card inside a card, two 32px paddings and a layer tree four tables deep. THE BLOCK MUST STAY, for a capability rather than a preference: a blank canvas has no other route to this structure, since reproducing it needs `align="center"` on the outer cell and `width="600"` on the inner table and neither is reachable from the Style rail, which edits CSS while those are legacy ATTRIBUTES (`margin:0 auto` is not a substitute, Outlook ignoring it). The name does not prevent a SECOND drop, and hiding the block once a card exists would need the block panel.
		label: 'Email card',
		category: BASIC,
		media: icon(
			'<rect x=".5" y=".5" width="23" height="13" rx="1.5" fill="currentColor" opacity=".1"/><rect x="5" y="2.5" width="14" height="9" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/>'
		),
		// A full-bleed outer table, an `align="center"` cell (not `margin:0 auto`, which Outlook ignores) and a 600px white inner table. The names say which surface was clicked. IT PAINTS NO PAGE COLOUR and must not start: the sheet behind it is the page, and a hardcoded band would be a grey stripe the author cannot reach.
		// IT CARRIES THE ACCENT BAR, the piece a hand rebuild could not reach. `app/email/shell.py::shell_html` opens every branded email with a 4px rule in the firm's colour, and a 4px row needs `height:4px` AND `font-size:0` AND `line-height:4px`, because Word floors a cell at its line box and the rule otherwise arrives ~16px tall. `{{ brand.accent }}` is the shipped form — the canvas drops the declaration, the send fills it in — and the `&nbsp;` stops clients collapsing the cell. The radius and inset MATCH THE SHIPPED SHELL (10px, `32px 32px 36px`), this block's whole claim being to BE that shell.
		content: `<table data-gjs-name="Email card" role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse"><tr data-gjs-name="Email card row"><td data-gjs-name="Card wrapper" align="center" style="padding:0"><table data-gjs-name="Card" role="presentation" width="600" border="0" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;border-collapse:collapse;background-color:#ffffff;border-radius:10px"><tr data-gjs-name="Accent bar row"><td data-gjs-name="Accent bar" style="height:4px;padding:0;font-size:0;line-height:4px;background-color:{{ brand.accent }};border-radius:10px 10px 0 0">&nbsp;</td></tr><tr data-gjs-name="Card row"><td data-gjs-name="Card content" style="padding:32px 32px 36px;height:48px;vertical-align:top"></td></tr></table></td></tr></table>`
	},
	// NOTE — NO MULTI-COLUMN LAYOUT, and that is the decision rather than a gap. Nothing in the Style rail can add or remove a `<td>`, so a block is the only route to columns — but every shipped default is a single 640px column, and a multi-column email's failure modes (Outlook collapsing a float, a 320px client stacking cells in an order nobody chose) are exactly the ones an author cannot see here and cannot fix from this rail. `SECTION_HTML` outlives the absence. `Email card` sits in Basic because a group heading over one block is a label pretending to be a category, and Spacer and Highlight box are absent for the same class of reason, each pinned by a test.

	// -- Message — the counterpart of the document rail's Letter/Regulatory groups: furniture pre-bound to merge fields so nobody hand-types a token. Only tokens EVERY send site provides (merge-fields.ts: `brand.*` is universal; a greeting addressed to a non-client carries no first_name). --
	{
		id: 'msg-greeting',
		label: 'Greeting',
		category: MESSAGE,
		media: icon(
			'<rect x="1" y="2" width="12" height="2" rx="1" fill="currentColor"/><rect x="1" y="6" width="18" height="1.5" rx=".75" fill="currentColor" opacity=".4"/><rect x="1" y="9.5" width="8" height="2" rx="1" fill="currentColor" opacity=".55"/>'
		),
		// `Hi`, and a FALLBACK on the name, both matching `email_defaults._greeting`. The fallback is not decoration: without it a send carrying no first name renders `Hi ,`, and the notifier has send sites with no recipient name at all (`task_notice` passes none). It is the FIELD form, not Jinja's `or`, so the panel's Fallback control can read and edit it — `{{ x or 'y' }}` is a token the picker refuses to offer a condition on (`fields.ts::fieldFromToken`).
		content: `<p data-gjs-type="text" data-gjs-name="Greeting" style="margin:0 0 16px;font-family:${FONT};font-size:15px;line-height:1.6;color:${INK}">Hi ${fieldSpanHtml('first_name', '', 'there')},</p>`
	},
	{
		id: 'msg-signoff',
		label: 'Sign-off',
		category: MESSAGE,
		media: icon(
			'<path d="M2 9.5c2.5-6 4.5 2.5 7-1.5s3.5 3.5 6 .5" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/><rect x="1" y="11.5" width="22" height="1.5" rx=".75" fill="currentColor" opacity=".3"/>'
		),
		content: `<p data-gjs-type="text" data-gjs-name="Sign-off" style="margin:24px 0 0;font-family:${FONT};font-size:15px;line-height:1.6;color:${MUTED_INK}">Best regards,<br />{{ brand.name }}</p>`
	},
	{
		id: 'msg-footer',
		label: 'Footer',
		category: MESSAGE,
		media: icon(
			'<rect x="1" y="1" width="22" height="6" rx="1" fill="currentColor" opacity=".12"/><line x1="1" y1="9" x2="23" y2="9" stroke="currentColor" stroke-width="1.2"/><rect x="1" y="11" width="12" height="2" rx=".75" fill="currentColor" opacity=".5"/>'
		),
		content: tbl(
			'Footer',
			row(
				'Footer row',
				// The separator and the support link are GUARDED, because a firm with no support address is the common case: `brand_for` passes it through exactly as stored, a placeholder having been removed on purpose so the preview cannot promise an address the send could not produce. Unguarded, this shipped a real footer reading "Graphene ·" plus an `href="mailto:"` with no address, which some clients render as a live but broken link (observed in an inbox). AN AUTHORED CONDITION, NOT HAND-WRITTEN JINJA — and the SPAN is guarded rather than the `<a>`, because the separator has to disappear with the address and a condition on the anchor alone leaves the dangling " · " this exists for.
				`<td data-gjs-name="Footer text" style="padding:16px 0 0;border-top:1px solid ${RULE};font-family:${FONT};font-size:${SMALL_SIZE};line-height:1.6;color:${MUTED_INK}">{{ brand.name }}<span data-gjs-name="Support address"${showWhen('brand.support_email', 'set')}> · <a data-gjs-name="Support link" href="mailto:{{ brand.support_email }}" style="color:${MUTED_INK};text-decoration:underline">{{ brand.support_email }}</a></span></td>`
			),
			'margin:24px 0 0'
		)
	}
];

// The masthead's FALLBACK, drawn as an image so the canvas can show it. A firm with no brand imagery sends its name set as type, and the masthead is one `<img>`, which renders a picture or a broken-image glyph and never styled text — so drawing the wordmark as an SVG is what lets the existing paint path show it, with no new mechanism. BASE64, not raw SVG: the value lands inside a quoted CSS `url("...")` and an SVG source is full of the quotes, spaces and parentheses `previewBrandMasthead` refuses. The width is an OVER-ESTIMATE on purpose, paired with `background-size: auto 100%`, so the height fixes the glyph size and the spare width is transparent; under `contain` an over-estimate would shrink the text instead.
export function fallbackWordmarkImage(name: string): { src: string; width: number } {
	const label = (name ?? '').trim().toUpperCase();
	if (!label) return { src: '', width: 0 };
	const esc = label
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
	// Arial bold uppercase runs about 0.72em per character; the tracking adds 0.09em on top. Rounded up.
	const width = Math.ceil(label.length * (12 * 0.72 + 12 * 0.09) + 8);
	const svg =
		`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="28">` +
		`<text x="0" y="19" font-family="Arial,Helvetica,sans-serif" font-size="12" ` +
		`font-weight="700" letter-spacing="1.08" fill="#64748b">${esc}</text></svg>`;
	try {
		// `unescape(encodeURIComponent(...))` is what makes a non-Latin-1 firm name encodable: `btoa` throws on one outright, and a firm named in Greek or Cyrillic must not break the preview.
		return {
			src: 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg))),
			width
		};
	} catch {
		// Unencodable for any reason: no paint. The dashed placeholder is the honest degradation.
		return { src: '', width: 0 };
	}
}

// Paint, in the canvas, the masthead outcome this firm will actually get. The block ships `src="{{ brand.logo_url }}"` and the canvas renders markup as-is, so without this a firm sees a broken-image glyph where its logo belongs. A class on the canvas body says which outcome applies (rules in `gjs-email-canvas.css`) and the real URL goes on a custom property, so the hidden `<img>` can be painted as a background; the `src` is left alone, being a token the export must keep. NOTHING HERE REACHES A RECIPIENT: both are set on the canvas document, not on what `getHtml()` returns. Returns a disposer, and re-applies on `load` / `canvas:frame:load` — the FRAME's lifecycle, which is the only thing that can take these off the body. It does NOT re-apply on a content load, and the note that used to say it did was wrong about the mechanism: `setComponents` re-renders the wrapper INSIDE the existing `document.body`, so the class and the custom property survive a starter layout, a default body and a code-view apply untouched. What the frame lifecycle does replace is the document itself, which is why both of those events are listened for. A change in the PROPS is a third thing again, and belongs to the caller: `email-editor.svelte` re-applies when the resolved brand actually changes.
export function previewBrandMasthead(
	editor: any,
	logoUrl?: string | null,
	fallbackName?: string | null
): () => void {
	// ANY SCHEME THE BROWSER CAN DRAW: https-only would be right at the send and wrong here, a deployment with no object-store bucket inlining branding uploads as `data:` URIs, so an https filter leaves a firm that HAS a logo looking at an empty box (whether a recipient gets it is answered separately, by `emailMastheadPreview`'s `mailable`). THE CHARACTER CHECK IS THE ONLY FILTER, and it is not an escaping problem: this value lands inside a quoted CSS declaration, so a quote ends the string early and a `)` ends `url(`. `CSS.escape` is the wrong tool, escaping for an IDENTIFIER and mangling every `/` and `:`. A base64 `data:` URI contains none of the three, so the common case passes; a raw `data:image/svg+xml,<svg ...>` is refused.
	const candidate = (logoUrl ?? '').trim();
	// A backslash is deliberately unchecked: it cannot terminate either, and `app/branding.py` has already validated the value server-side.
	const unsafe = candidate.includes('"') || candidate.includes(')') || candidate.includes(' ');
	const url = candidate && !unsafe ? candidate : '';
	const fallback = url ? { src: '', width: 0 } : fallbackWordmarkImage(fallbackName ?? '');
	const painted = url || fallback.src;
	// THIS RUNS BEFORE THE MASTHEAD EXISTS, so no "has it resolved yet" state is needed and a class
	// gating one would be inert. Measured against the GrapesJS source rather than assumed: `apply()`
	// is called synchronously after `grapesjs.init` returns, and the `canvas:frame:load` re-apply
	// fires inside the frame's own `onload` BEFORE `renderHead`, before the canvas script chain and
	// before `renderBody` appends the component tree. By the time the element is in the document the
	// body already carries `has-brand-logo` and `--brand-logo-preview`.
	//
	// WHAT IS LATE IS THE STYLESHEET, and that is a different problem this cannot solve. `styles:
	// ['/gjs-email-canvas.css']` is script-appended into an already-parsed about:blank document, so
	// it is not render-blocking: the frame paints once with NO canvas CSS at all, for one network
	// round trip — a fresh one on every open while developing, because the URL is cache-busted per
	// page load. A rule inside that sheet cannot dress the window in which the sheet is missing, so
	// what the masthead shows there is decided entirely by its own `src`: `MASTHEAD_PLACEHOLDER`, a
	// TRANSPARENT pixel. It was the generic grey rectangle, which is what flashed.
	const apply = (): void => {
		const body = editor?.Canvas?.getBody?.() as HTMLElement | undefined;
		if (!body) return;
		// `has-brand-logo` means "there is something to paint", not "the firm has a logo": both arms hide the contents and show the background, and only the SIZING differs.
		body.classList.toggle('has-brand-logo', !!painted);
		body.classList.toggle('brand-logo-is-fallback', !url && !!fallback.src);
		// The painted name's own width. An `<img>` whose `src` cannot load has NO intrinsic width, so without this the background was drawn into a 28px box and the name clipped to its first letter.
		body.style.setProperty(
			'--brand-logo-fallback-width',
			fallback.width ? `${fallback.width}px` : 'auto'
		);
		body.style.setProperty('--brand-logo-preview', painted ? `url("${painted}")` : 'none');
	};
	apply();
	editor.on?.('load', apply);
	editor.on?.('canvas:frame:load', apply);
	return () => {
		editor.off?.('load', apply);
		editor.off?.('canvas:frame:load', apply);
	};
}

// Conditional text is WITHDRAWN as a feature — a field's own `| fallback(...)` filter and the block condition cover it. The type stays so a STORED one still parses and renders; nothing authors a new one.
export const CHOICE_TYPE = 'email-choice';

// The `email-choice` type: a conditional text as ONE node. Spelt out, a mid-sentence choice is five nodes interleaved with the words around them; Jinja's own inline conditional says it in one expression. `editable: false` because the text is a PREVIEW of one branch, so typing into it would change the canvas and nothing about what is sent — the compile step discards the children. NOTHING AUTHORS ONE: there is no panel and no insert, and the only way to introduce one is a hand edit in the code view, which `conditions/compile.ts` then honours rather than letting loose Jinja reach a client. The type is registered so such a node still parses and still compiles.
export function registerChoice(editor: any): void {
	editor.DomComponents?.addType?.(CHOICE_TYPE, {
		isComponent: (el: HTMLElement) =>
			el?.getAttribute?.('data-gjs-type') === CHOICE_TYPE ? { type: CHOICE_TYPE } : undefined,
		model: {
			defaults: {
				name: 'Conditional text',
				// Inline, so it must not be a drop target: a block landing inside would be discarded by the compile step along with the preview text.
				droppable: false,
				editable: false,
				draggable: true,
				removable: true
			}
		}
	});
}

// RE-EXPORTED, NOT REDECLARED. `conditions/attrs.ts` owns these names because it owns `EDITOR_ATTRS`, the list the export must strip — and a name declared twice can be stripped in one place and not the other.
export { BRAND_ARM_ATTR, BRAND_ARM_LOGO } from './conditions/attrs.js';
export { FIELD_ATTR, FIELD_FALLBACK_ATTR, FIELD_FILTER_ATTR } from './conditions/attrs.js';

/**
 * For TEXT content, where `escapeAttr` would be wrong.
 *
 * Only `&`, `<` and `>` — deliberately NOT quotes. The string this protects is a Jinja fallback
 * sitting inside `fallback('…')` in the element BODY, so escaping `'` would replace the delimiters
 * Jinja parses and the template would not compile at all.
 *
 * The entity survives the render and the mail client draws the character, so an author who types
 * `<none>` sees `<none>` in the email — which is what they asked for. Unescaped, the browser parsed
 * it as a tag while the span was being written and the fallback was simply gone.
 */
function escapeText(text: string): string {
	return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(text: string): string {
	return text
		.replace(/&/g, '&amp;')
		.replace(/"/g, '&quot;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/'/g, '&#39;');
}

export function fieldSpanHtml(
	field: string,
	filter?: string | null,
	fallback?: string | null
): string {
	const path = field.trim();
	const token = fieldToken(path, filter, fallback);
	if (token === '') return '';
	const name = (filter ?? '').trim();
	const spare = fallback ?? '';
	return (
		// `contenteditable="false"` is what actually makes the token uneditable, and the model's `editable: false` is not a substitute: the flag stops GrapesJS opening an RTE ON the span, while the span sits INSIDE a text block whose own RTE makes the element contenteditable — so without the attribute the caret walks through the token and an author can delete a brace, after which `{{ first_nam }}` renders as nothing and `{{ first_name` as literal braces in a client's email. A `contenteditable="false"` island is atomic to the browser, which is the right unit.
		// NAMED FOR THE FIELD, not the kind: a generic "Field" leaves two chips in one paragraph distinguishable only by reading the token through the chip.
		`<span data-gjs-type="email-field" data-gjs-name="${escapeAttr(labelForField(path) ?? path)}" contenteditable="false"` +
		` ${FIELD_ATTR}="${path}"` +
		(name === '' ? '' : ` ${FIELD_FILTER_ATTR}="${name}"`) +
		(spare === '' ? '' : ` ${FIELD_FALLBACK_ATTR}="${escapeAttr(spare)}"`) +
		// THE BODY TOKEN CARRIES AN HTML-ESCAPED FALLBACK. `sel.components(token)` PARSES this string,
		// so a fallback of `<none>` became a bogus element — the chip read `fallback('')`, the saved html
		// held a stray tag, and the sent email never rendered the fallback while the panel (reading the
		// escaped attribute) still showed it as set. `</span><img src=x onerror=…>` was parsed and
		// persisted. Escaped before `fieldToken` composes it, so its own Jinja escaping still applies.
		`>${fieldToken(path, filter, escapeText(spare))}</span>`
	);
}

// The field and filter a selected component carries, or null when it is not one of ours. Reads the ATTRIBUTES, falling back to the text: a pasted or hand-written span has none, and refusing to describe it would leave the author with a token the panel pretends not to see.
export function readFieldSpan(
	component: any
): { field: string; filter: string; fallback: string } | null {
	if (!component) return null;
	const attrs = component.getAttributes?.() ?? {};
	const field = String(attrs[FIELD_ATTR] ?? '').trim();
	if (field !== '') {
		return {
			field,
			filter: String(attrs[FIELD_FILTER_ATTR] ?? '').trim(),
			// NOT trimmed: a fallback of a single space keeps a sentence's spacing where a name would have been, and is a real answer. Absent reads as '', which means "no fallback".
			fallback: String(attrs[FIELD_FALLBACK_ATTR] ?? '')
		};
	}
	return parseFieldToken(component.get?.('content') ?? component.toHTML?.());
}

// A merge field as a COMPONENT rather than loose text. You cannot drag a token you cannot pick up, and `{{ amount }}` renders `1234.5` where the firm means £1,234.50 — so `| currency` has to be reachable, and the panel can only offer it against a selection. `data-field` is the structured truth and the span's text is its rendering: the text is what Jinja evaluates at send, the attribute is what the panel reads without parsing copy, and every write goes through `fieldSpanHtml` so they cannot come apart. ADDITIVE — a plain-text token still works exactly as it did, so nothing migrates.
export function registerEmailField(editor: any): void {
	editor.DomComponents?.addType?.('email-field', {
		// MATCHED ON `data-field`, NOT ON `data-gjs-type`, and that is the whole atomicity guarantee. A field inserted into a text block — every field typed with the `{{` picker — is committed by the RTE, and the re-parse REWRITES `data-gjs-type` to `text` and strips `contenteditable` (measured: `{type: 'text', editable: true, contenteditable: null}`), so keyed on the type the claim would never be made and the token would become ordinary editable text. `data-field` survives that re-parse; `data-gjs-type` is still accepted for a span parsed straight from stored HTML.
		isComponent: (el: HTMLElement) => {
			const isField =
				!!el?.getAttribute?.(FIELD_ATTR) || el?.getAttribute?.('data-gjs-type') === 'email-field';
			return isField ? { type: 'email-field' } : undefined;
		},
		model: {
			defaults: {
				name: 'Field',
				droppable: false,
				editable: false,
				// RE-ASSERTED FROM THE MODEL, because GrapesJS drops the attribute when it re-parses RTE-committed content (measured: `contenteditable` null on a field typed with the `{{` picker). Declaring it here puts it back on every render, however the span arrived.
				attributes: { contenteditable: 'false' },
				// NOT DRAGGABLE. An inline span whose drag is resolved by the sorter's geometry is the same problem `appendBlockOnClick` routes around, and Resend does not offer it either. A field is moved by editing the copy around it, which is how you move a word.
				draggable: false,
				removable: true
			}
		}
	});
}

/** What stands in for an image whose `src` is a merge token — and it READS AS A PICTURE.
 *
 *  Not a broken-image glyph and not GrapesJS's own placeholder, both of which say "this image is
 *  broken" about an image that is perfectly fine. It was a FLAT FILLED RECTANGLE, which is the
 *  other failure: a solid grey box says nothing about what it stands for, so an author reads it as
 *  a fault in the editor rather than as "a picture arrives here at send". A framed picture glyph
 *  says it, and says it in the same visual language as the unset brand slot in
 *  `gjs-email-canvas.css`, which is drawn from the same shapes.
 *
 *  It stays OPAQUE-FREE on purpose — an outline and a glyph, no fill — because a replaced image
 *  paints over its own background: the brand masthead is painted BEHIND this element by the canvas
 *  sheet, and a filled rectangle hid it (`content: ''` in that sheet is the other half). */
const TOKEN_IMAGE_PLACEHOLDER =
	'data:image/svg+xml;charset=utf-8,' +
	encodeURIComponent(
		'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="40">' +
			// A BARE `#`: the whole string goes through `encodeURIComponent`, so a pre-encoded `%23` is
			// encoded again into `%2523`, the fill is invalid and the placeholder renders solid black.
			'<rect x="0.8" y="0.8" width="158.4" height="38.4" rx="4" fill="none" ' +
			'stroke="#cbd5e1" stroke-width="1.6" stroke-dasharray="5 4"/>' +
			'<rect x="59" y="11" width="42" height="18" rx="2" fill="none" stroke="#94a3b8" ' +
			'stroke-width="1.5"/>' +
			'<circle cx="68" cy="17.5" r="2" fill="#94a3b8"/>' +
			'<path d="M62 26.5l6.5-6 3.6 3.3 3.2-2.6 5.2 5.3" fill="none" stroke="#94a3b8" ' +
			'stroke-width="1.5" stroke-linejoin="round"/>' +
			'</svg>'
	);

/** The masthead's own stand-in: ONE FULLY TRANSPARENT PIXEL.
 *
 *  The masthead is the one token image whose real answer the canvas already knows — the firm's
 *  logo, painted as a background by `gjs-email-canvas.css` from `--brand-logo-preview`. Anything
 *  drawn INTO the element covers that, because a replaced element paints its image over its own
 *  background. The sheet removes the replaced image with `content: ''`, but `content` on a replaced
 *  element is Chromium/WebKit behaviour rather than a spec guarantee, so this is the belt to that
 *  brace: in an engine that ignores `content`, a transparent pixel still shows the logo through,
 *  where the framed glyph above would sit on top of it. */
const MASTHEAD_PLACEHOLDER =
	'data:image/svg+xml;charset=utf-8,' +
	encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>');

/** Does this source want a merge field resolved before it is a URL at all? */
export function isTokenSource(src: unknown): boolean {
	const text = typeof src === 'string' ? src : '';
	return text.includes('{{') || text.includes('{%');
}

export function registerMastheadImage(editor: any): void {
	// SCOPED TO ITSELF. The wordmark fallback used to ride a GENERIC `data-fallback`, so it was not the
	// brand logo's at all: duplicate the masthead and the compiled email carried a SECOND
	// `{% if brand.logo_url %}…{% else %}wordmark{% endif %}`, and a stray copy of the attribute on any
	// image would have produced a wordmark where no wordmark belongs.
	//
	// `copyable: false` is the load-bearing half — there is one masthead, and a second is not a thing an
	// author can want. The type also names it, so the layer list and the rail agree on what it is.
	editor.DomComponents?.addType?.('masthead-image', {
		extend: 'email-image',
		extendView: 'email-image',
		isComponent: (el: HTMLElement) =>
			el?.tagName === 'IMG' && el.getAttribute?.(BRAND_ARM_ATTR) === BRAND_ARM_LOGO
				? { type: 'masthead-image' }
				: undefined,
		model: {
			defaults: {
				name: 'Brand logo',
				copyable: false,
				draggable: true,
				removable: true
			}
		}
	});
}

/**
 * DOUBLE-CLICKING AN IMAGE DOES NOTHING, on purpose.
 *
 * GrapesJS's image view opens the asset manager from `onActive`, which its own `events` map binds to
 * `dblclick`. There is no asset manager here — the library was removed and the rail owns an image's
 * source — and `assetManager: { custom: true }` stops the manager RENDERING, not OPENING: the modal
 * shell mounted with nothing in it and closed again, which reads as a flicker on every double-click.
 *
 * Registered on the BUILT-IN `image` type, and BEFORE `email-image`/`masthead-image`, which extend
 * its view: one override inherited by all three, so a plain uploaded picture behaves like a token
 * one. `addType` with an id already registered extends that type, so nothing else about an image is
 * redefined here.
 *
 * `stopPropagation` is KEPT from the original: without it a double-click on an image inside a text
 * cell bubbles and starts editing the cell.
 */
export function registerImageActivation(editor: any): void {
	editor.DomComponents?.addType?.('image', {
		view: {
			onActive(this: unknown, ev?: Event): void {
				ev?.stopPropagation?.();
			}
		}
	});
}

export function registerEmailImage(editor: any): void {
	// AN IMAGE WHOSE SOURCE IS A MERGE FIELD MUST NOT BE FETCHED. `{{ brand.logo_url }}` is not a URL,
	// so the canvas resolved it as a RELATIVE PATH: a 404 on every editor open, and the browser's
	// broken-image glyph where the firm's logo belongs — which reads as "the logo is broken" when
	// nothing is wrong. It is only unresolved because the canvas is not a send.
	//
	// THE MODEL IS NEVER TOUCHED, which is the whole safety argument: GrapesJS exports an image from
	// its model, so the token reaches the recipient exactly as authored. Only the VIEW is given a
	// placeholder — the same separation `email-field` relies on, one layer down.
	// THE BUILT-IN IMPLEMENTATION, captured BEFORE the override and called by identity — never via
	// `this.constructor.__super__`. `masthead-image` extends `email-image`, so on a masthead instance
	// `__super__.updateSrc` resolves to THIS override again and calls itself: a real URL on the
	// masthead blew the stack (`RangeError: Maximum call stack size exceeded`). It only fired for a
	// NON-token source, which is why every earlier test — all of them on `{{ brand.logo_url }}` —
	// passed.
	const baseUpdateSrc = editor.DomComponents?.getType?.('image')?.view?.prototype?.updateSrc;

	editor.DomComponents?.addType?.('email-image', {
		extend: 'image',
		extendView: 'image',
		isComponent: (el: HTMLElement) =>
			el?.tagName === 'IMG' && isTokenSource(el.getAttribute?.('src'))
				? { type: 'email-image' }
				: undefined,
		model: {
			defaults: {
				// Everything an image is, plus a name that says why it looks empty here.
				name: 'Image (from a merge field)'
			}
		},
		view: {
			/** GrapesJS's own `updateSrc` writes `model.get('src')` onto the element. Intercepted for a
			 *  token, and ONLY for a token — a real URL still renders, so an author who replaces the
			 *  merge field with a hosted image sees it immediately. */
			updateSrc(this: any): void {
				const src = this.model?.get?.('src');
				if (!isTokenSource(src)) return baseUpdateSrc?.call(this);
				const el = this.el as HTMLImageElement | undefined;
				if (!el) return;
				// THE MASTHEAD IS THE ONE TOKEN IMAGE WHOSE ANSWER THE CANVAS HAS. `masthead-image`
				// extends this view, so it inherited the generic stand-in and a firm WITH a logo saw
				// that stand-in instead of its logo — the canvas paints the logo BEHIND the element
				// and a replaced image covers its own background. A transparent pixel lets it
				// through whatever the engine does with the sheet's `content: ''`.
				el.src =
					el.getAttribute(BRAND_ARM_ATTR) === BRAND_ARM_LOGO
						? MASTHEAD_PLACEHOLDER
						: TOKEN_IMAGE_PLACEHOLDER;
				// Said in the tooltip rather than drawn: the canvas is the email, and a caption of ours
				// inside it would be chrome the recipient never sees.
				el.title = `Filled in at send from ${String(src).trim()}`;
			}
		}
	});
}

export function registerBrandLogo(editor: any): void {
	editor.DomComponents?.addType?.('brand-logo', {
		isComponent: (el: HTMLElement) =>
			el?.getAttribute?.('data-gjs-type') === 'brand-logo' ? { type: 'brand-logo' } : undefined,
		model: {
			defaults: {
				name: 'Brand logo',
				droppable: false,
				editable: false,
				highlightable: false,
				// LEGACY ONLY. Nothing emits `data-gjs-type="brand-logo"` any more, in either language, the masthead being one image with a fallback now. What this still catches is a template SAVED BEFORE that change, whose stored html wraps a hand-written `{% if %}...{% else %}...{% endif %}`; without the type those fragments parse as loose siblings and an author can delete half the conditional. The protection is ONE-SHOT even there: `highlightable: false` means GrapesJS does not re-emit `data-gjs-type`, so the attribute is gone from the next save and `isComponent` never matches again.
				components: [],
				selectable: true
			},
			init(this: any) {
				const lock = (component: any): void => {
					component.set?.({
						selectable: false,
						hoverable: false,
						editable: false,
						draggable: false,
						removable: false,
						copyable: false,
						layerable: false
					});
					component.components?.()?.each?.(lock);
				};
				this.components?.()?.each?.(lock);
				// A parse populates children AFTER init, so the lock has to run again when they arrive.
				this.listenTo?.(this.components?.(), 'add reset', () => this.components?.()?.each?.(lock));
			}
		}
	});
}

// Install the palette. `select: true` on every block (the preset's default too) is what makes the Style rail useful: a drop that left nothing selected would land the author on an empty inspector.
export function registerEmailBlocks(editor: any): void {
	const bm = editor.BlockManager;
	for (const { id, ...def } of EMAIL_BLOCKS) bm.add(id, { select: true, ...def });
}

// WHERE A DROP LANDS. The visible canvas is almost entirely a table and its cells, and GrapesJS's table types refuse everything else: table `['tbody','thead','tfoot']` (grapes.mjs 0.21.13 :33811), row `['th','td']` (:34080), cell `true` (:33933).
// A REFUSED TARGET IS NOT A DEAD DROP, which is what makes the symptom hard to name. `dimsFromTarget` re-runs against the parent ELEMENT whenever the target is invalid (:50191-50194; `containerSel` is `*`, :30751) and that walk only stops at the wrapper — so a pointer over the table rather than a cell silently PROMOTES the drop to body level, draws its indicator hundreds of pixels away, and lands the block outside the 600px card. Which reads exactly like "it won't let me drop here". `droppable: true` ON THE TABLE IS HALF A FIX: a `<p>` as a direct child of `<table>` is invalid HTML, hoisted out on parse and mangled by Word, so the canvas would look right and the inbox would not. Hence both halves — WIDEN the target (`widenDropTargets`) so the indicator is drawn where the pointer is, and CORRECT the landing (`correctedDropTarget` -> `correctDroppedComponent`) into a `<td>`. ONE UNDO STEP falls out of doing that synchronously: `Components.onAdd` fires `component:add` from INSIDE the sorter's own collection add (:27681-27700) and backbone-undo groups by callstack (`magicFusionIndex`, :98-114), so the add, our remove and our re-add fuse into one entry.
// THE GATE IS THE ACTION LABEL, and it is exact rather than defensive. The Sorter stamps `'add-component'` for a palette block and `'move-component'` for an in-canvas drag (:50640, :50656), where `Component.append`/`move` stamp `'component:add'`/`'component:move'` (:18754-18756) and a parse stamps nothing. So the correction cannot re-enter through its own writes, and cannot rewrite a STORED template on open — `<body><p>...` parses fine, so a correction at parse time would silently restructure a saved email and mark it dirty just for being looked at. position.ts's `move`-index trap does not apply: `Component.move` short-circuits only within the SAME parent (:30334-30337).

// The two `action` labels the Sorter stamps on a drop — the whole of what may be corrected. See above for why our own writes and a parse must not be. Exported so the gate is assertable.
export const DROP_ACTIONS: readonly string[] = ['add-component', 'move-component'];

// Component types whose built-in `droppable` is narrowed to table plumbing, widened so the sorter's upward walk stops at the element under the pointer. These are GrapesJS TYPE IDS, not tags — `<tr>` is `row` (grapes.mjs :36680) — and `cell` is absent, already being `droppable: true`. `draggable` is left alone deliberately: it still guards the plumbing, so opening a table to CONTENT does not open it to a stray cell or row, which would produce markup no correction below could repair.
export const WIDENED_DROP_TARGETS: readonly string[] = ['table', 'tbody', 'thead', 'tfoot', 'row'];

// Tags that ARE the table plumbing. Never relocated: they are already where the parent expects them, and "correcting" one is an infinite regress, every wrapper the correction builds being itself a table, a row and a cell.
const TABLE_STRUCTURE = new Set([
	'thead',
	'tbody',
	'tfoot',
	'tr',
	'td',
	'th',
	'caption',
	'colgroup',
	'col'
]);

// What each structural parent may legally hold. The descent from a table to a cell is this map applied repeatedly, which is why `correctedDropTarget` answers ONE hop at a time.
const CONTENT_SLOTS: Record<string, readonly string[]> = {
	table: ['tbody', 'thead', 'tfoot'],
	tbody: ['tr'],
	thead: ['tr'],
	tfoot: ['tr'],
	tr: ['td', 'th']
};

export interface DropSite {
	parent: string;
	child: string;
	at: number;
	siblings: string[];
}

export type DropPlan =
	{ action: 'keep' } | { action: 'wrap' } | { action: 'into'; sibling: number; end: boolean };

// The corrected destination for one level. Pure, so the decision table is assertable without an editor — and it is a decision table: every branch is "already legal" or "here is the child that can hold it".
export function correctedDropTarget(site: DropSite): DropPlan {
	const { parent, child, siblings } = site;
	const at = Number.isFinite(site.at) ? Math.max(0, site.at) : 0;
	if (TABLE_STRUCTURE.has(child)) return { action: 'keep' };
	if (parent === 'wrapper') {
		// A drop on the email body. A bare `<p>` there is valid HTML but not email markup: it sits outside the 600px card, so it renders full-bleed and un-centred. A table needs no wrapping, and that covers more blocks than it looks like — every layout block plus Divider, Note Box, Button and Footer.
		return child === 'table' ? { action: 'keep' } : { action: 'wrap' };
	}
	const slots = CONTENT_SLOTS[parent];
	if (!slots) return { action: 'keep' };
	const holders = siblings
		.map((tag, index) => ({ tag, index }))
		.filter((holder) => slots.includes(holder.tag));
	// Nothing to descend into. Unreachable from this palette (GrapesJS's table constructor inserts a `tbody`, its `tbody` a row and a cell, :33806, :33862-33880), but a defined answer beats a crash on a shape we did not author.
	if (!holders.length) return { action: 'keep' };
	// How many holders the pointer got past. ONE rule for all three levels: before the first => the start of the first, past the last => the END of the last, else the start of the one it stopped at.
	const passed = holders.filter((holder) => holder.index < at).length;
	const end = passed >= holders.length;
	const holder = (end ? holders[holders.length - 1] : holders[passed]) ?? holders[0];
	return { action: 'into', sibling: holder?.index ?? 0, end };
}

// The email body answers `wrapper` as its TYPE; everything else is identified by tag, because tags are what HTML validity is about (`row`/`cell` are type ids, and a component built from a definition object may carry no type at all).
function dropKind(component: any): string {
	if (component?.get?.('type') === 'wrapper') return 'wrapper';
	return String(component?.get?.('tagName') ?? '').toLowerCase();
}

// A component's children as a plain array: `components()` is a Backbone collection whose iteration protocol is not something to depend on across versions, while `map` has been on it throughout.
function childList(component: any): any[] {
	return component?.components?.()?.map?.((child: any) => child) ?? [];
}

// The first cell in a freshly-built section, found by walking: the parser inserts the implied `<tbody>`, so the cell is three levels down and its depth is not ours to hardcode.
function firstCell(component: any): any {
	for (const child of childList(component)) {
		const kind = dropKind(child);
		if (kind === 'td' || kind === 'th') return child;
		const found = firstCell(child);
		if (found) return found;
	}
	return null;
}

// table -> tbody -> tr -> td is three hops; the bound exists so a type that ever answered CONTENT_SLOTS in a cycle would end the drop rather than hang the editor.
const MAX_DROP_HOPS = 6;

// Where a dropped component should end up — `null` when it already landed somewhere legal, which is the overwhelming case. Reads the model and does NOT mutate it: the relocation has to be a single `move` to be a single undo entry, so the whole descent is resolved first and applied once.
export function resolveDropDestination(
	component: any
): { destination: any; at: number; wrap: boolean } | null {
	let parent = component?.parent?.();
	let at = component?.index?.() ?? 0;
	for (let hop = 0; parent && hop < MAX_DROP_HOPS; hop += 1) {
		const siblings = childList(parent).filter((child) => child !== component);
		const plan = correctedDropTarget({
			parent: dropKind(parent),
			child: dropKind(component),
			at,
			siblings: siblings.map(dropKind)
		});
		if (plan.action === 'wrap') return { destination: parent, at, wrap: true };
		// `hop` distinguishes "it landed legally" from "we descended to here": a keep at hop 0 is nothing to do, a keep further down is the destination we walked to.
		if (plan.action === 'keep') return hop ? { destination: parent, at, wrap: false } : null;
		const next = siblings[plan.sibling];
		if (!next) return hop ? { destination: parent, at, wrap: false } : null;
		parent = next;
		at = plan.end ? childList(next).length : 0;
	}
	return null;
}

export function correctDroppedComponent(component: any): void {
	const target = resolveDropDestination(component);
	if (!target) return;
	if (!target.wrap) {
		component.move?.(target.destination, { at: target.at });
		return;
	}
	// The section goes in AT the component's own index, pushing the component to `at + 1`; the move then takes it back out, leaving the section where the indicator was. Both writes are in this callstack, so one ctrl+z removes the section too.
	const section = target.destination.append?.(SECTION_HTML, { at: target.at })?.[0];
	const cell = section && firstCell(section);
	if (!cell) {
		// The section did not build. Leave the component where it landed — valid HTML, only the width is lost — and take the half-built band away. Undoing the author's own drop would be worse.
		section?.remove?.();
		return;
	}
	component.move?.(cell, { at: 0 });
}

// Route every drop to a cell. No disposer, like `configureStyleRail`'s subscription: the event bus belongs to the editor's own model, so `editor.destroy()` takes the handler with it. The page-global trap in this file is keymaster's handler map (see UNDO), not `editor.on`.
export function installDropCorrection(editor: any): void {
	editor.on?.('component:add', (component: any, opts?: { action?: string }) => {
		if (!DROP_ACTIONS.includes(opts?.action ?? '')) return;
		correctDroppedComponent(component);
	});
}

// The canvas widths, and the ONE place they are written down. GrapesJS sizes the frame from this list, and the editor's own layout sizes the document HEAD to match, because the head sits OUTSIDE the frame and GrapesJS cannot narrow it — unmatched, Mobile shrinks the email to 375px under a 640px header, which reads as the preview being broken. `widthMedia` is a breakpoint, not a canvas width, kept as the preset declared it even though `css_inline` runs `keep_at_rules=False` so no at-rule survives to the inbox.
export const EMAIL_DEVICES = [
	{ id: 'desktop', name: 'Desktop', width: '640px', widthMedia: '' },
	{ id: 'mobile', name: 'Mobile portrait', width: '375px', widthMedia: '480px' }
] as const;

export function deviceWidth(name: string): string {
	return EMAIL_DEVICES.find((device) => device.name === name)?.width ?? EMAIL_DEVICES[0].width;
}

/** Marks the one cell that IS the page, so `ensurePageCell` can tell its own work from an author's. */
export const PAGE_ATTR = 'data-email-page';

// The page's default inset and height. BOTH ARE REAL, SERIALISED STYLES — a canvas-only floor would put the author's background or padding in one box here and another in the inbox. `height` ON A CELL IS THE EMAIL-SAFE WAY, and `min-height` is NOT: Word ignores it. `height` on a `<td>` is honoured everywhere and behaves as a floor, so the page is 600px when the copy is short and taller when it is long, identically in the editor and the inbox. The inset figures are `app/email/shell.py::shell_html`'s own, so an untouched page reads as the house style.
export const PAGE_INSET = '32px 32px 36px';
export const PAGE_MIN_HEIGHT_PX = 600;

// The page: a full-width, one-cell table everything else lives inside. `vertical-align:top` matters — a cell taller than its content centres it, and a half-empty email would float in the middle.
export const PAGE_HTML =
	`<table data-gjs-name="Page" ${PAGE_ATTR}="true" role="presentation" width="100%" border="0" ` +
	'cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse">' +
	`<tr><td data-gjs-name="Page" ${PAGE_ATTR}="true" style="height:${PAGE_MIN_HEIGHT_PX}px;` +
	`padding:${PAGE_INSET};vertical-align:top">` +
	'</td></tr></table>';

// Set the page cell's own inset, chiefly to stand it DOWN when the content being loaded brings its own shell: a page inset around a full band-plus-card pushes the band off the edges it should reach. The starter layout needs no stand-down — it is content, not a shell — and a test pins that.
export function setPageInset(editor: any, value: string): void {
	setPageStyle(editor, { padding: value });
}

// A declaration that names no colour reads as UNSET. `none` is GrapesJS's own default for `background-color` and what its rail leaves when a colour is cleared, but it is not a valid `background-color` at all — passed through it lights the row's "this property is set" accent over a control showing the word `none`. `transparent` is NOT folded away: it is a real answer and the only way to clear a fill, the same distinction `isIndirectColour` draws.
function colourOrBlank(value: unknown): string {
	const text = String(value ?? '').trim();
	return text === '' || text.toLowerCase() === 'none' ? '' : text;
}

// The PAGE's own style: what colour the sheet is, and how far in from its edges the copy sits. IT WRITES TO THE PAGE CELL, which is only sound because that cell's height and inset are real serialised styles (see `PAGE_INSET`). The page is deliberately NOT selectable (`protectPage`), so these cannot be reached through the style rail — which is why the rail's idle state offers them instead of a "select something" hint, as Resend's own Page style panel does.
export interface PageStyle {
	background: string;
	padding: string;
}

export function readPageStyle(editor: any): PageStyle {
	const style = pageCell(editor)?.getStyle?.() ?? {};
	return {
		// `background-color` is what the rail's colour control writes, so the two agree about which property a "background" is. A hand-set `background` shorthand is read too: an author who typed one should see it.
		background: colourOrBlank(style['background-color'] ?? style.background),
		padding: String(style.padding ?? '')
	};
}

// WHAT THIS PANEL DELIBERATELY DOES NOT OFFER: a page font-family or text colour. Both look like the
// obvious page-level controls and both are a lie in an inbox — Outlook's Word engine does not inherit
// either onto a child element, so an author would set a typeface, see it in the canvas, and the
// recipient would read the client's default serif. Every block declares its own font for that reason
// (`FONT` in each block's markup), which is the same rule `UNMAILABLE_STYLE_PROPERTIES` enforces.
export function setPageStyle(
	editor: any,
	patch: Partial<PageStyle>,
	opts?: { partial?: boolean }
): void {
	const cell = pageCell(editor);
	if (!cell?.addStyle) return;
	const next: Record<string, string> = {};
	// An empty string CLEARS: GrapesJS drops a declaration set to '' rather than writing `padding:`, which is what makes the panel's clear affordance mean what it says.
	if (patch.padding !== undefined) next.padding = patch.padding;
	if (patch.background !== undefined) next['background-color'] = patch.background;
	// `partial` is a value STILL BEING CHOSEN — every pointer move of a colour drag. Written
	// untracked through `UndoManager.skip`, the same mechanism `ensurePageCell` and `seed` use, so a
	// drag through a hundred shades leaves the canvas correct and the undo stack untouched. The
	// caller sends one tracked write when the gesture ends, and that is what ctrl+z returns from.
	// Without this, picking a page background put one entry on the stack PER POINTER MOVE.
	const um = editor?.UndoManager;
	if (opts?.partial && um?.skip) um.skip(() => cell.addStyle(next));
	else cell.addStyle(next);
}

// Guarantee that the whole page is a CELL, so a block can be dropped anywhere on it. A drop only reliably lands in a `<td>` that already exists: the refusal happens inside GrapesJS's own target resolution, upstream of anything `widenDropTargets` or `correctDroppedComponent` can see, so this removes the case rather than correcting it. An outer container table is standard email markup anyway.
// IDEMPOTENT, and it has to be: it runs on every load, and a second page nested in the first would halve the usable width each time. The marker attribute distinguishes its own work from an author's — a name would not, an author being able to rename anything. Call it AFTER seeding content and BEFORE the change listeners attach, so the wrap is part of the saved baseline rather than an edit the author did not make.
export function ensurePageCell(editor: any): void {
	// UNTRACKED, because the wrap is BOOKKEEPING. The re-wrap MOVES every top-level child into the new page cell and a `move` is a remove plus an add, so the starter layout's ten nodes would put TEN entries on the stack for one click and ctrl+z would take the email apart a paragraph at a time. `UndoManager.skip` pauses tracking and restores whatever it was (grapes.mjs :61373), so it is safe on the load path too.
	const um = editor?.UndoManager;
	if (um?.skip) {
		um.skip(() => ensurePageCellNow(editor));
		return;
	}
	ensurePageCellNow(editor);
}

function ensurePageCellNow(editor: any): void {
	const wrapper = editor?.getWrapper?.();
	const kids = wrapper?.components?.();
	if (!wrapper || !kids) return;
	const children = childList(wrapper);
	// Already wrapped: exactly one child, and it is ours. Still protected — a page loaded from saved HTML is parsed as an ordinary table, so its protections live here and not in the document.
	if (children.length === 1 && children[0]?.getAttributes?.()?.[PAGE_ATTR] === 'true') {
		protectPage(children[0], firstCell(children[0]));
		return;
	}
	const page = wrapper.append?.(PAGE_HTML, { at: 0 })?.[0];
	const cell = page && firstCell(page);
	if (!cell) {
		page?.remove?.();
		return;
	}
	// In order, so the email reads the same afterwards. `move` rather than re-parsing, so the components keep their ids, their styles and their own attributes.
	children.forEach((child, index) => child.move?.(cell, { at: index }));
	protectPage(page, cell);
}

// The page is CHROME, not content — the posture the document editor's `doc-page` takes, and it matters more here. `ensurePageCell` runs on LOAD, so an author who deletes the page loses the drop-anywhere behaviour for the rest of the session, arriving mid-edit as "you cannot drag blocks onto the page" with no obvious cause; copying it would nest a second page inside the first, halving the usable width.
// THE CELL IS THE PAGE. Everything above it — the table and the `<tbody>`/`<tr>` the table type inserts — is locked, because those implied components carry no name: GrapesJS labels one "Row", so a click there reports a Row with a move/copy/delete toolbar that is neither the page nor anything an author put there. It stops at the FIRST `<td>` and never descends, everything inside being the author's content. Set on the models rather than declared as a component type: an `isComponent` matching the attribute would resolve `<table data-email-page>` to a type that is NOT the built-in `table`, losing the constructor that inserts the implied `<tbody>` and the droppable list its rows depend on.
function protectPage(table: any, cell: any): void {
	const chrome = {
		draggable: false,
		copyable: false,
		removable: false,
		selectable: false,
		hoverable: false,
		toolbar: []
	};
	const lockChain = (component: any, depth: number): void => {
		if (!component || depth > 6) return;
		if (dropKind(component) === 'td') return;
		component.set?.(chrome);
		for (const child of childList(component)) lockChain(child, depth + 1);
	};
	lockChain(table, 0);
	cell?.set?.({
		// NAMED HERE, not only in PAGE_HTML: the parser CONSUMES `data-gjs-*`, so the name is not in the saved HTML and a reloaded page would badge as "Cell". `PAGE_ATTR` survives because it is not a `data-gjs-` attribute, which is also why it, and not the name, identifies the page.
		name: 'Page',
		draggable: false,
		copyable: false,
		removable: false,
		droppable: true,
		// NOT SELECTABLE, and not hoverable: the rail's IDLE state IS the Page style panel, so a selectable cell gives one surface two ways to be styled and they disagree — selecting the page would show every border composite and corner radius against a surface whose whole authorable design is a background and an inset, while the panel headed "Page style" shows only when it is NOT selected. `droppable` is untouched: a drop target need not be selectable.
		selectable: false,
		hoverable: false,
		toolbar: []
	});
}

// The page's own cell, or the wrapper if it is somehow absent, so a click still puts the block SOMEWHERE. WALKED OVER THE MODELS, NOT `find()`: `Component.find` resolves its selector against the RENDERED element, so before the frame's views exist it answers an empty list indistinguishable from "there is no page" — at which point every caller takes the wrapper fallback and a clicked block is aimed outside the page. `childList` + `firstCell` is pure model, and answers the same before and after render.
export function pageCell(editor: any): any {
	const wrapper = editor?.getWrapper?.();
	const page = childList(wrapper).find((child) => child?.getAttributes?.()?.[PAGE_ATTR] === 'true');
	return (page && firstCell(page)) ?? wrapper;
}

// Whether the author's canvas holds nothing yet — what the "Starter layout" and "Load default" offers are gated on. IT ASKS THE PAGE CELL, NOT THE WRAPPER: `ensurePageCell` puts a page table in on every load, so a wrapper-child count is 1 on a brand-new template and both offers are unreachable for ever. The page is CHROME (see `protectPage`), so counting it as content is the same error as counting the frame.
export function isCanvasEmpty(editor: any): boolean {
	const cell = pageCell(editor);
	return (cell?.components?.()?.length ?? 0) === 0;
}

// Add a block by CLICKING it, rather than dragging: a second way in that involves no geometry at all, and therefore the one an author can rely on whatever a drag does. Not a workaround — `appendOnClick` is GrapesJS's own hook, and for a single-column email "click it and it appears at the end" is arguably the better interaction.
// WHERE IT LANDS: the selected cell when there is one, so an author working inside a cell carries on there; otherwise the page cell, at the end. The one thing it must never do is land out of sight. The select/activate dance mirrors `BlockManager.__endDrag` (grapes.mjs :40668-40690) so a clicked block behaves like a dropped one, and `select: false` is still honoured.
export function appendBlockOnClick(editor: any, block: any): void {
	const content = block?.get?.('content');
	if (!content) return;
	const selected = editor?.getSelected?.();
	const target = dropKind(selected) === 'td' ? selected : pageCell(editor);
	const added = target?.append?.(content)?.[0];
	if (!added) return;
	const activate = !!block.get?.('activate');
	const select = block.get?.('select');
	if (select || (activate && select !== false)) editor.select?.(added, { activate });
	else if (activate) added.trigger?.('active');
	editor.Canvas?.scrollTo?.(added, { behavior: 'smooth' });
}

// Make a drag FROM THE PALETTE resolve its target the same way an in-canvas drag does. THE SYMPTOM: a component already on the page drags and reorders perfectly, while the same block dragged in from the palette places nothing unless it lands in a `<td>` that already exists. Two drags, two code paths. The in-canvas sorter is fed by the events `FrameView` re-dispatches out of the iframe onto the frame element (grapesjs :30938-30944), so its coordinates are the PARENT document's, which is what `positionOptions.canvasRelative` expects; `Droppable`'s own sorter is fed by the HTML5 `dragover` it binds on the iframe's BODY (:56648), so its coordinates are the IFRAME's, read as though they were the parent's. Everything upstream is fine — verified in the running editor, `canMove` answers OK for both the wrapper and the page cell — the target simply resolves to the wrong place.
// `Droppable.startCustom()` is the missing piece: it installs `getSorterOptions`, giving that sorter a `customTarget` (`document.elementFromPoint` against the IFRAME's own document). `BlockManager.startDrag` calls it; `BlockView.handleDragStart`, which is what the native HTML5 drag goes through, calls only `__startDrag` — so this bridges the two on GrapesJS's own public events rather than by patching the view. The pairing is safe: by `block:drag:stop`, `handleDragEnd` has already set `Droppable.over` to false, so `endCustom` only detaches listeners and cannot drop a second time.
export function installPaletteDropFix(editor: any): void {
	const frames = (): any[] =>
		((editor?.Canvas?.getFrames?.() ?? []) as any[]).map((frame) => frame?.view).filter(Boolean);
	editor.on?.('block:drag:start', () => {
		for (const view of frames()) view.droppable?.startCustom?.();
	});
	editor.on?.('block:drag:stop', () => {
		for (const view of frames()) view.droppable?.endCustom?.();
	});
}

// Widen the built-in table types' `droppable` for THIS editor, so a drop stops where the pointer is instead of being promoted to the body. Per-editor, and checked rather than hoped: `ComponentManager` builds its `componentTypes` array AND the entry objects in its own constructor (grapes.mjs :36673), so `addType` reassigning an entry's `model` cannot reach another editor on the page — which matters because the other global this file touches behaves the opposite way (destroying any editor wipes keymaster's page-wide map, see UNDO), and a read-only editor mounted beside an editable one must not inherit droppable tables.
// `addType` on an existing id EXTENDS it: the `defaults` getter spreads ours over the built-ins (:37013-37019) and carries `isComponent` over, so `<table>` still resolves to the table type. The `getType` guard stops a GrapesJS rename turning that extension into a NEW phantom type whose `isComponent` matches nothing: not widened is a degradation, a phantom type is a bug.
export function widenDropTargets(editor: any): void {
	const dc = editor?.DomComponents;
	for (const type of WIDENED_DROP_TARGETS) {
		if (!dc?.getType?.(type)) continue;
		dc.addType(type, { model: { defaults: { droppable: true } } });
	}
}

// LightningCSS-safe core GrapesJS stylesheet plus the shadcn re-theme of every GrapesJS surface. A static `<link>` because Vite/LightningCSS rejects GrapesJS's IE `filter` syntax when the CSS is imported. Every block is idempotent (guarded by element id) and keys off the app's theme tokens.
function ensureEmailEditorStyles(): void {
	if (!document.getElementById('gjs-css')) {
		const link = document.createElement('link');
		link.id = 'gjs-css';
		link.rel = 'stylesheet';
		link.href = '/grapesjs.css';
		document.head.appendChild(link);
	}
	if (!document.getElementById('gjs-email-layout')) {
		const s = document.createElement('style');
		s.id = 'gjs-email-layout';
		s.textContent = [
			// Zero the built-in panel gutters, and STRIP THE DARK BACKGROUND: `EditorView.render` puts `gjs-one-bg gjs-two-color` on this element unconditionally (grapes.mjs :69646-69647) and `gjs-one-bg` is the framework's dark chrome, which left standing paints a near-black band either side of the email card while the head above stays light. So the backdrop is declared in exactly ONE place, the canvas column, and everything GrapesJS renders inside it is see-through.
			'.gjs-editor{--gjs-left-width:0px;--gjs-canvas-top:0px;--gjs-right-width:0px;background:transparent!important}',
			'.gjs-frame-wrapper__top{display:none!important}',
			'.gjs-off-prv{display:none!important}',
			// THE CANVAS IS NOT THE SCROLLER. The email's head has to scroll WITH the email and share its centre line, and it can do neither from inside this element: everything in here is absolutely positioned (`.gjs-cv-canvas__frames` is a full-size layer, `.gjs-frame-wrapper` centred by `margin:auto` on an absolute box), so an in-flow sibling is simply PAINTED OVER — verified, a head inserted here vanishes behind the card. The ONE scroller is the canvas COLUMN, which owns the backdrop, the gutter and the scrollbar; this element becomes a plain, exactly-email-tall box, `syncFrameHeight` sizing the MOUNT that `.gjs-editor` and this both inherit `height:100%` from. GrapesJS supports the arrangement rather than merely tolerating it: `listenToEl` defaults to the container's parentNode and re-offsets the canvas on that element's scroll (:49496-49506 -> `onContainerChange` -> `em.refreshCanvas()`), which keeps badges and toolbars on their components.
			'.gjs-cv-canvas{overflow:visible!important;background:transparent!important;padding:0!important;display:block!important}',
			// The white card is as tall as the EMAIL, not the viewport: `height:auto` here, with the real pixel height driven by `syncFrameHeight()` (an iframe never sizes to its own document). `min-height` is load-bearing — the wrapper is `overflow:hidden`, so without a floor a sync that has not run yet collapses it to 0 and CLIPS THE CANVAS BLANK.
			`.gjs-cv-canvas__frames{height:auto!important;min-height:${MIN_FRAME_HEIGHT}px!important;transform:none!important;transform-origin:top center!important}`,
			`.gjs-frame-wrapper{height:auto!important;min-height:${MIN_FRAME_HEIGHT}px!important;box-shadow:0 6px 24px rgba(15,23,42,.12)!important;border-radius:8px!important;overflow:hidden!important}`,
			`.gjs-frame{height:auto!important;min-height:${MIN_FRAME_HEIGHT}px!important;display:block!important;background:#fff!important}`,
			// The head is the same KIND of object as the card below it — a white sheet of the same radius and shadow — which is what makes the two read as one document, and it is declared next to `.gjs-frame-wrapper` precisely so the two treatments cannot drift. TREATMENT ONLY: its width comes from `syncHeadWidth` and its POSITION from the component's own stylesheet, because this sheet is injected once and guarded by element id, so a hot reload does NOT replace it and layout kept here would go stale under the very edits being made to it.
			'.gjs-email-head{background:#fff!important;border-radius:8px!important;box-shadow:0 6px 24px rgba(15,23,42,.12)!important;overflow:hidden!important;box-sizing:border-box!important}',
			'.gjs-margin-v-el,.gjs-fixedmargin-v-el{display:none!important}'
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-email-blocks')) {
		// Light-themed, 2-column block panel (GrapesJS's own is dark), matching the document editor's rail so the two palettes are one design. SCOPED BY A CLASS, not by `#gjs-blocks-panel` as the document editor's copy is, and the STYLESHEET id is namespaced for the same reason: sharing either would let whichever editor mounts first in a session style the other.
		const P = '.gjs-email-palette';
		const s = document.createElement('style');
		s.id = 'gjs-email-blocks';
		s.textContent = [
			`${P}, ${P} .gjs-blocks-cs, ${P} .gjs-block-categories, ${P} .gjs-blocks-c, ${P} .gjs-blocks-no-cat{background:transparent!important;color:var(--foreground)!important;}`,
			`${P} .gjs-block-category{background:transparent!important;border:none!important;}`,
			`${P} .gjs-title{font-size:9px!important;padding:10px 6px 4px!important;font-weight:700!important;letter-spacing:.08em!important;text-transform:uppercase!important;color:var(--muted-foreground)!important;background:transparent!important;cursor:pointer!important;user-select:none!important;border:none!important;border-top:1px solid var(--border)!important;display:flex!important;align-items:center!important;gap:6px!important;}`,
			`${P} .gjs-block-category:first-child .gjs-title{border-top:none!important;}`,
			// A CLOSED CATEGORY IS HIDDEN OUTRIGHT, not dimmed: the rail is what switches groups, so its segments plus a stack of dimmed headers are two controls for one job — and the headers were the ones that looked broken. The OPEN one keeps its header, which reads as the flyout's title.
			`${P} .gjs-block-category:not(.gjs-open){display:none!important;}`,
			// And its header is a label, not a button: left clickable it would collapse the only visible group, which the rule above then hides, leaving an empty flyout and no way back.
			`${P} .gjs-title{pointer-events:none!important;}`,
			`${P} .gjs-caret-icon{display:none!important;}`,
			// `.gjs-open` scoping is load-bearing: GrapesJS collapses a category by setting `display:none` INLINE on its blocks container, and a stylesheet `!important` outranks a plain inline style — so an unscoped `display:grid!important` makes every category permanently open and clicking a header does nothing. Same rule as the document rail.
			`${P} .gjs-block-category.gjs-open .gjs-blocks-c{display:grid!important;grid-template-columns:1fr 1fr!important;gap:1px!important;padding:1px 0 4px!important;}`,
			`${P} .gjs-block{padding:8px 6px!important;min-height:0!important;height:auto!important;width:auto!important;box-sizing:border-box!important;display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;gap:5px!important;border-radius:6px!important;border:1px solid var(--border)!important;background:var(--muted)!important;color:var(--foreground)!important;cursor:grab!important;transition:background .1s,border-color .1s,color .1s!important;}`,
			`${P} .gjs-block.gjs-one-bg{background:var(--muted)!important;color:var(--foreground)!important;}`,
			`${P} .gjs-block:hover, ${P} .gjs-block.gjs-four-color-h:hover{background:var(--accent)!important;border-color:var(--border)!important;box-shadow:none!important;color:var(--foreground)!important;}`,
			`${P} .gjs-block__media{display:flex!important;align-items:center!important;justify-content:center!important;height:20px!important;overflow:hidden!important;flex-shrink:0!important;color:var(--muted-foreground)!important;}`,
			`${P} .gjs-block__media svg{width:28px!important;height:18px!important;display:block!important;}`,
			`${P} .gjs-block-label{font-size:10px!important;font-weight:500!important;line-height:1.2!important;text-align:center!important;color:inherit!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;max-width:100%!important;}`
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-email-rte')) {
		const s = document.createElement('style');
		s.id = 'gjs-email-rte';
		s.textContent = [
			'.gjs-rte-toolbar{background:var(--popover)!important;border:1px solid var(--border)!important;border-radius:8px!important;box-shadow:0 2px 12px rgba(0,0,0,0.13),0 1px 3px rgba(0,0,0,0.08)!important;padding:2px!important;overflow:visible!important;}',
			'.gjs-rte-actionbar{display:flex!important;align-items:center!important;gap:0!important;padding:0!important;}',
			'.gjs-rte-action{min-width:28px!important;height:28px!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;background:transparent!important;color:var(--foreground)!important;border:none!important;border-radius:5px!important;padding:0 6px!important;font-size:14px!important;font-weight:700!important;cursor:pointer!important;transition:background .1s!important;}',
			'.gjs-rte-action:hover{background:var(--accent)!important;color:var(--foreground)!important;}',
			'.gjs-rte-action.gjs-rte-active{background:var(--brand-subtle)!important;color:var(--brand-strong)!important;}'
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-email-chrome')) {
		const s = document.createElement('style');
		s.id = 'gjs-email-chrome';
		s.textContent = [
			'.gjs-cv-canvas .gjs-highlighter,.gjs-cv-canvas .gjs-highlighter-sel{outline-color:var(--brand)!important;}',
			// NO NAME BADGE. GrapesJS labels the hovered component with a little tab restating the obvious, in its own vocabulary for the ones it guesses at, while the selection OUTLINE already says what is selected without covering the line below. Hidden in CSS rather than by `badgable: false` per type, because the flag is per component — one rule covers a plain `<p>` GrapesJS infers, and whatever a preset upgrade adds.
			'.gjs-badge,.gjs-com-badge{display:none!important;}',
			'.gjs-toolbar{display:block!important;background:var(--brand)!important;border-radius:6px!important;overflow:hidden!important;box-shadow:0 2px 8px rgba(15,23,42,.22)!important;}',
			'.gjs-toolbar-items{display:flex!important;align-items:center!important;}',
			'.gjs-toolbar-item{width:auto!important;min-width:26px!important;height:26px!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;padding:0 6px!important;color:var(--brand-foreground)!important;cursor:pointer!important;transition:background .1s!important;}',
			'.gjs-toolbar-item:hover{background:color-mix(in oklch,var(--brand-foreground) 18%,transparent)!important;}',
			'.gjs-toolbar-item svg{fill:currentColor!important;width:15px!important;height:15px!important;}'
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-email-modal')) {
		const s = document.createElement('style');
		s.id = 'gjs-email-modal';
		s.textContent = [
			'.gjs-mdl-backdrop{background:color-mix(in oklch,var(--hero-base) 45%,transparent)!important;}',
			'.gjs-mdl-dialog{background:var(--background)!important;color:var(--foreground)!important;border:1px solid var(--border)!important;border-radius:12px!important;box-shadow:0 20px 50px rgba(15,23,42,.28)!important;overflow:hidden!important;}',
			'.gjs-mdl-header{background:transparent!important;border-bottom:1px solid var(--border)!important;padding:13px 16px!important;display:flex!important;align-items:center!important;}',
			'.gjs-mdl-title{color:var(--foreground)!important;font-size:14px!important;font-weight:600!important;}',
			'.gjs-mdl-btn-close{color:var(--muted-foreground)!important;font-size:22px!important;opacity:1!important;transition:color .12s!important;}',
			'.gjs-mdl-btn-close:hover{color:var(--foreground)!important;}',
			'.gjs-mdl-content{background:var(--background)!important;color:var(--foreground)!important;padding:16px!important;}'
		].join('');
		document.head.appendChild(s);
	}
}

// Lock a GrapesJS editor into view-only. It ships no read-only mode, so — the /edit route being reachable by an `email_templates:read`-only principal — the document itself is neutralised: every component non-editable/movable/droppable/removable, the mutating keymaps stripped, late arrivals too.
function applyReadOnly(editor: any): void {
	const lockTree = (): void => {
		const lock = (c: any): void => {
			c?.set?.(
				{
					editable: false,
					draggable: false,
					droppable: false,
					removable: false,
					copyable: false,
					badgable: false
				},
				{ avoidStore: true }
			);
			c?.components?.()?.forEach?.(lock);
		};
		lock(editor.getWrapper?.());
	};
	lockTree();
	editor.on('load', lockTree);
	editor.on('component:add', (c: any) =>
		c?.set?.(
			{ editable: false, draggable: false, droppable: false, removable: false, copyable: false },
			{ avoidStore: true }
		)
	);
	editor.Keymaps?.removeAll?.();
}

// Load stored email content — the round trip the save contract (schema.html / schema.css) relies on. ORDER IS LOAD-BEARING: styles first, components second. `EditorModel.setStyle` is `cssComposer.clear()` then an add, so it wipes the WHOLE rule collection; called AFTER the parse it deletes any rule the parse had just derived from the markup and lands the content unstyled. It is worst for the shipped defaults: `email/defaults.py::default_for` returns an EMPTY css, so a trailing `setStyle(css)` is a bare `clear()`. This order is right for both stored shapes — a legacy body's id-keyed rules are installed first and still match the ids the parse assigns, while a modern inline-styled body carries its declarations on the elements, where `clear()` cannot reach them.
export function loadEmailContent(editor: any, data: { html?: string; css?: string } | null): void {
	editor.setStyle(data?.css ?? '');
	editor.setComponents(data?.html ?? '');
}

// A canvas asset's URL, cache-busted while developing. The canvas stylesheet and script live in each app's `static/`, so they are plain URLs the iframe fetches — outside Vite's module graph, outside HMR, and cached like any other file. Fine in production and actively misleading in development: an edit does not appear, and the only clue is that the file on disk disagrees with the screen. The stamp is per PAGE LOAD, so both assets re-fetch together, and `import.meta.env.DEV` is substituted at build time so a production bundle carries the bare path.
const CANVAS_ASSET_STAMP = (import.meta as { env?: { DEV?: boolean } }).env?.DEV
	? `?dev=${Math.trunc(performance.now())}`
	: '';

function canvasAsset(path: string): string {
	return `${path}${CANVAS_ASSET_STAMP}`;
}

// Minimum height of the canvas FRAME. A blank draft should read as an email page rather than a sliver. Derived from `PAGE_MIN_HEIGHT_PX` rather than restating 600: the page carries that height as a real serialised style, so the frame has to be at least as tall or the card would clip the page it holds.
export const MIN_FRAME_HEIGHT = PAGE_MIN_HEIGHT_PX;

// Size the canvas iframe to its CONTENT (floored at MIN_FRAME_HEIGHT), so the white card is the length of the email rather than of the viewport: an iframe never sizes to its own document, and GrapesJS's `height: '100%'` makes the card fill the canvas whatever the email's length. Returns a disposer — the ResizeObserver watches a node inside the frame.
export function syncFrameHeight(editor: any): () => void {
	// Resolve the frame from the DOM, not `Canvas.getFrameEl()`: at the point this is wired the canvas API can still answer undefined, and an early bail there left the height unset for good.
	const frameEl = (): HTMLIFrameElement | null =>
		(editor?.Canvas?.getFrameEl?.() as HTMLIFrameElement | null) ??
		(editor?.getContainer?.()?.querySelector('iframe.gjs-frame') as HTMLIFrameElement | null) ??
		null;

	// Last height written, so a measurement that has not really changed is a no-op. A ResizeObserver on a node whose size we then set is a feedback loop by construction; this is the brake.
	let lastApplied = -1;

	const apply = (): void => {
		const frame = frameEl();
		const body = frame?.contentDocument?.body;
		if (!frame || !body) return;
		// scrollHeight, not offsetHeight: the body is the scrolling box, so offsetHeight would report back whatever we last set and the card could never shrink again.
		const h = Math.max(Math.ceil(body.scrollHeight), MIN_FRAME_HEIGHT);
		if (Math.abs(h - lastApplied) <= 1) return;
		lastApplied = h;
		// `setProperty(..., 'important')`: the layout rules above declare `height:auto!important` to beat GrapesJS's own inline sizing, and a stylesheet !important outranks a plain inline style — so a plain assignment is silently ignored and the card sticks at min-height, clipping content.
		frame.style.setProperty('height', `${h}px`, 'important');
		const wrapper = frame.parentElement as HTMLElement | null;
		wrapper?.style.setProperty('height', `${h}px`, 'important');
		// AND THE MOUNT. The canvas column is the scroller now, which only scrolls if the editor is as tall as the email: `.gjs-editor` and `.gjs-cv-canvas` are both `height:100%`, so the mount is the one measurement they derive from, and without this a long email is cut off at the fold.
		editor?.getContainer?.()?.style?.setProperty('height', `${h}px`, 'important');
		// NOTHING SIZES THE PAGE FROM HERE, and nothing may: the page carries a REAL `height` (see `PAGE_HTML`), so it fills the sheet in the editor and the inbox for the same reason. Injecting `[data-email-page]{min-height:Npx}` into the canvas head is what makes a page background fill the sheet here and paint a band round the copy in the email.
	};

	let observer: ResizeObserver | null = null;
	const observeBody = (): boolean => {
		const body = frameEl()?.contentDocument?.body;
		if (!body || typeof ResizeObserver === 'undefined') return false;
		observer = new ResizeObserver(() => apply());
		observer.observe(body);
		return true;
	};

	let tries = 0;
	let timer: ReturnType<typeof setTimeout> | null = null;
	const attach = (): void => {
		apply();
		if (observeBody() || ++tries > 20) return;
		timer = setTimeout(attach, 100);
	};
	attach();

	editor.on('update', apply);
	editor.on('load', attach);
	return () => {
		if (timer) clearTimeout(timer);
		observer?.disconnect();
		editor.off?.('update', apply);
		editor.off?.('load', attach);
	};
}

// Keep the email's HEAD exactly as wide as the frame. The head is rendered by `email-editor.svelte` in the canvas COLUMN, above the mount, centred with `margin: 0 auto`; the card is centred inside `.gjs-cv-canvas` — the full width of that same column — by `margin: auto` on an absolute box. Two boxes of the same width centred in the same space share a centre line, so only the width is left to match.
// IT MUST NOT BE MOVED INTO `.gjs-cv-canvas`: everything there is absolutely positioned and `.gjs-cv-canvas__frames` is a full-size layer that paints over any in-flow sibling, so a head placed inside disappears. Injecting into GrapesJS's own subtree also puts the node at the mercy of `CanvasView.template()`, which sets that element's innerHTML. WIDTH IS MEASURED, NOT ASSUMED: reading it off the wrapper means a third device needs no list kept in step. Returns a disposer.
export function syncHeadWidth(editor: any, head: HTMLElement): () => void {
	const wrapper = (): HTMLElement | null =>
		editor?.getContainer?.()?.querySelector('.gjs-frame-wrapper') ?? null;

	let lastWidth = -1;
	const apply = (): void => {
		const w = wrapper()?.offsetWidth ?? 0;
		// A wrapper that has not been laid out measures 0. Writing that would collapse the head to nothing, and no later event would necessarily fix it, so 0 means "not known yet".
		if (w <= 0 || w === lastWidth) return;
		lastWidth = w;
		head.style.width = `${w}px`;
	};

	let observer: ResizeObserver | null = null;
	let tries = 0;
	let timer: ReturnType<typeof setTimeout> | null = null;
	const attach = (): void => {
		apply();
		const target = wrapper();
		if (target && typeof ResizeObserver !== 'undefined') {
			observer = new ResizeObserver(() => apply());
			observer.observe(target);
			return;
		}
		if (++tries > 20) return;
		timer = setTimeout(attach, 100);
	};
	attach();

	// A device switch changes the width without changing the email, so the cached value has to go.
	const onDevice = (): void => {
		lastWidth = -1;
		apply();
	};
	editor.on?.('load', onDevice);
	editor.on?.('change:device', onDevice);
	editor.on?.('canvas:frame:load', onDevice);

	return () => {
		if (timer) clearTimeout(timer);
		observer?.disconnect();
		observer = null;
		editor.off?.('load', onDevice);
		editor.off?.('change:device', onDevice);
		editor.off?.('canvas:frame:load', onDevice);
	};
}

export interface CreateEmailEditorOpts {
	container: HTMLElement;
	blocksEl: HTMLElement;
	readOnly?: boolean;
}

// There is no `stylesEl`: `styleManager` is `custom: true`, so GrapesJS renders nothing and there is nothing to mount — the rail is `style-panel/`, in Svelte. Nor a `brandColours` option, since nothing reaches Spectrum any more; the firm's colours are a prop on `ColorInput`, and a swatch writes the colour as a LITERAL so a later rebrand does not reach templates already authored.

// Style properties an email client will not honour, removed from the rail the preset builds. Offering them is worse than offering nothing: the canvas renders them faithfully, so the author sees the effect and the recipient does not.
// Deliberately NARROW — several properties look unsafe and are kept because they are genuine email technique. `text-shadow` is unsupported essentially everywhere. `background` is Spectrum's layered stack (gradients and images), which Outlook renders not at all, while `background-color` stays and covers the case that works. `min-height` is the trap: Word honours `height` on a `<td>` AS a minimum, which is how vertical space is built here, and does not honour `min-height` at all — kept, the rail would offer two controls reading as the same promise, and the author picks the one whose NAME says "at least this tall", which is the one that does nothing. `border-collapse` is not unsupported but not an authoring decision: `collapse` is the only value an email should use and every table here declares it inline.
// PRUNING `background` IS WHY NO AUTHORED MARKUP MAY DECLARE IT (pinned by a test, the shorthand being the more natural thing to type): the rail's colour control writes `background-color`, so a block shipping `background:#f3f7fe` is not EDITED by it — the rail appends a competing declaration, the element exports both, and the orphan is unclearable because the control that could clear it is the one pruned here.
export const UNMAILABLE_STYLE_PROPERTIES: readonly (readonly [string, string])[] = [
	['typography', 'text-shadow'],
	['decorations', 'background'],
	['dimension', 'min-height'],
	['decorations', 'border-collapse']
];

// PER-SIDE BORDERS, the mirror image of UNMAILABLE_STYLE_PROPERTIES. The preset ships exactly one border control, the COMPOSITE `border`, which writes all four sides at once — so every single-side border our own blocks author is unreachable: the Note Box's `border-left` stripe (which IS the block's identity), the Divider's `border-top` (which IS the block) and the Footer's separator. Worse than unreachable, it is a TRAP: an author opening Border to recolour the Note Box stripe gets a box on all four sides.
// EMAIL SAFETY DECIDES WHICH WAY TO FIX IT. A per-side border on a `<td>` is standard, load-bearing email technique that Word honours, so there is nothing to prune and the answer is to make the properties editable — ALL FOUR SIDES, not the three the blocks happen to use, since `border-right` being the only unused side makes "sides we use" mean "every side but one". NOT OFFERED, on the same reasoning: `groove`/`ridge`/`inset`/`outset`, which Word renders inconsistently, leaving `none`/`solid`/`dashed`/`dotted`/`double` — `none` included on purpose, being what switches the whole BOX off (a single edge goes off by clearing its WIDTH, which `style-panel/borders.ts` then removes rather than leaving `border-left: 0 solid …`); and `rem`/`vh`/`vw` widths, which Word does not support and which mean nothing inside a fixed card.
// FIVE COMPOSITES, THREE ROWS ON SCREEN: each is three sub-controls, so `style-panel/borders.ts` gathers the family into a four-up `BoxSides` width, one Style select and one Colour. THE COLLAPSE IS PRESENTATION ONLY, and has to be — two checks against grapesjs 0.23.4 rule out a model-level version. (1) It cannot read what our own blocks ship: `__styleHasProps` (:63730) answers only for the composite's own name and sub-names and `__getPropsFromStyle` (:63767) returns null otherwise, so a `border-width` composite sees nothing in `border-left: 3px solid #3b82f6` and the stripe reads back as UNSET. (2) It writes a CSS form we cannot vouch for in Outlook: `border-width`/`-style`/`-color` are a different family from the per-edge shorthand, and nothing establishes that Word honours them. So the panel writes back THROUGH these composites, every declaration being a per-edge shorthand of exactly the shape the blocks author.

// The property the four sides must sit beside: they ARE its per-edge form. It lives in COLOUR_SECTOR — see that banner for why every border control belongs there whole.
export const BORDER_ANCHOR = 'border';

// The default border width, used in three places for one reason: a colour with no width draws nothing. See `borderSideProperty` (whose note covers the preset's own `0`) and `style-panel/borders.ts`, which reaches for it when a style-or-colour-only edit has no width to hang on.
export const BORDER_WIDTH_DEFAULT = '1px';

export const BORDER_STYLE_DEFAULT = 'solid';

export const BORDER_SIDES = ['top', 'right', 'bottom', 'left'] as const;

export interface StylePropertyDef {
	property: string;
	label: string;
	type?: string;
	default?: string;
	min?: number;
	max?: number;
	step?: number;
	units?: readonly string[];
	options?: readonly { id: string; label: string }[];
	properties?: readonly StylePropertyDef[];
}

const BORDER_STYLE_OPTIONS = ['none', 'solid', 'dashed', 'dotted', 'double'].map((id) => ({
	id,
	label: id.charAt(0).toUpperCase() + id.slice(1)
}));

// One side's composite, shaped to read as the `border` control an author already understands: the same three sub-controls in the same order. `detached` stays at its default `false`, which is the whole point: a non-detached composite writes the SHORTHAND and blanks the longhands, so an edit REPLACES the declaration the block authored rather than adding a second beside it — which is why the blocks must keep declaring `border-left:` and not `border-left-color:` (pinned by a test).
// The width default is `1px`, NOT the preset's `0`: a non-detached composite joins its sub-properties `withDefault`, so a colour-only edit would produce `border-left: 0 solid #b91c1c`, a control that took the colour and drew nothing. Labels are `label` and nothing overrides them: `Property.getLabel()` consults `i18n['styleManager.properties.<id>']` FIRST, and GrapesJS keys its own sub-property names with a `-sub` suffix while ours are plain property names — so naming a sub-property `border-width` here would silently pick up GrapesJS's label. Same trap as the `i18n[name]` collision under NAMES.
function borderSideProperty(side: string): StylePropertyDef {
	return {
		property: `border-${side}`,
		label: `Border ${side}`,
		type: 'composite',
		properties: [
			{
				property: `border-${side}-width`,
				label: 'Width',
				type: 'number',
				default: BORDER_WIDTH_DEFAULT,
				min: 0,
				units: ['px', 'em']
			},
			{
				property: `border-${side}-style`,
				label: 'Style',
				type: 'select',
				default: 'solid',
				options: BORDER_STYLE_OPTIONS
			},
			{ property: `border-${side}-color`, label: 'Colour', type: 'color', default: 'black' }
		]
	};
}

// THE BACKGROUND & BORDERS SECTOR. Every control that paints a fill or a border lives in ONE sector, because "give this thing a background" and "put a rule under it" should not require knowing which CSS family the intent belongs to. Text colour is the deliberate exception — see `MOVED_COLOUR_PROPERTIES`.
// THE BORDER CONTROLS MOVE WHOLE, NOT JUST THEIR COLOURS. A `border-{side}-color` longhand here with the width and style left behind is unsafe both ways: a non-detached composite writes the SHORTHAND and BLANKS its own longhands (grapes.mjs 0.21.13 :56209-56245), so a width edit in the other sector deletes what a longhand here had written; and CSS's cascade means `border-left: 3px solid red` RESETS `border-left-color`, so with both in one style attribute the answer depends on which came first. Our blocks author the shorthand, so both WOULD be present — and nothing establishes that Word honours the longhand while a shorthand sits beside it carrying a different colour. Making the composites `detached` does not rescue it: a detached composite writes only the ONE sub-property that changed (:56262-56271), leaving the block's authored shorthand standing beside the new longhand.
// The one arrangement with exactly ONE declaration per edge is a single composite owning the whole shorthand. So the four sides sit here intact, and the sector is named for what it HOLDS: a sector called 'Colours' containing a width control would be the same species of lie as a control the inbox drops. A colour-only split IS reachable with a `toStyle`/`fromStyle` pair and is deliberately not shipped — it cannot be verified from tests alone and its failure mode is a control that destroys borders. THE PRESET'S OWN `border` LIVES HERE TOO and gains the width default the four sides carry; moving the MODEL rather than re-declaring the property keeps its sub-property labels, units and types, and sidesteps the `-sub` i18n collision noted above. A MOVE IS A REAL SUBTRACTION, so what a source sector is left with is checked rather than assumed: a property belongs to exactly ONE sector, `removeProperty` returning the undestroyed model (:59918), so `decorations` ends up empty and is removed outright (`FOLDED_SECTOR`).

export const COLOUR_SECTOR = 'colours';
export const COLOUR_SECTOR_LABEL = 'Background & borders';

// The preset's own sectors, renamed to what an author is trying to DO: `Dimension` and `Typography` are the words a stylesheet uses, and a compliance officer editing an invite is asking about "the spacing" and "the text". `decorations` IS NOT IN THIS LIST, and its absence is load-bearing: `FOLDED_SECTOR` removes that sector outright once its last control has moved, so a rename entry would change nothing. Renaming the MODEL, because `Sector.getName()` is what both the panel and its tests read; a sector the preset renames or drops is simply not found and keeps its name.
export const SECTOR_RENAMES: readonly (readonly [string, string])[] = [
	['dimension', 'Spacing & size'],
	['typography', 'Text']
];

// The sector FOLDED AWAY, and the control that comes with it. `decorations` is down to `border-radius` once every colour and border has moved out — one control behind its own collapsed heading, in a section whose name an author would not think to open for it — and it belongs in `Background & borders` on the merits, `border-radius` being a border property. APPENDED AFTER the four sides so the border family reads shorthand -> per-edge -> shape, which is why this is its own list rather than more entries in `MOVED_COLOUR_PROPERTIES`, whose loop runs first.
export const FOLDED_SECTOR = 'decorations';

export const FOLDED_PROPERTIES: readonly (readonly [string, string | null])[] = [
	['border-radius', 'Corner radius']
	// `border-collapse` is deliberately NOT folded in here: it is pruned outright (`UNMAILABLE_STYLE_PROPERTIES`), because an author has no choice to make about it.
];

// Controls MOVED into the `Background & borders` sector, in the order they end up in the rail: `[fromSector, id, relabel]`, a `null` relabel keeping the preset's own name. THE ORDER IS THE CONTRACT: background comes first because it is what an author opening that section is looking for, and `border` comes LAST because the four per-side composites are appended behind it — that appending is the only thing keeping the all-sides shorthand next to its per-edge forms, so moving `border` up this list silently separates them. The relabel is not tidying: the preset calls `background-color` just 'Background', which in this sector reads as the background IMAGE we prune.
export const MOVED_COLOUR_PROPERTIES: readonly (readonly [string, string, string | null])[] = [
	// TEXT COLOUR IS DELIBERATELY NOT IN THIS LIST: gathering colours together splits a different intent in half, an author restyling a heading wanting its SIZE and its COLOUR, which would then sit in two sectors at opposite ends of the rail. It keeps its relabel, applied in place — `PROPERTY_RENAMES`.
	['decorations', 'background-color', 'Background colour'],
	['decorations', BORDER_ANCHOR, null]
];

// Property labels in the author's words, overridden through I18N rather than on the model: `Property.getLabel()` consults `i18n['styleManager.properties.<id>']` FIRST and GrapesJS ships an entry for every property it defines, so `set('name', ...)` succeeds, reports the new string, and still renders the old one. The trap `SECTOR_RENAMES` documents one level up.
export const PROPERTY_RENAMES: readonly (readonly [string, string])[] = [
	// Plain `Color` beside Font size reads as "the colour of what?"; in a sector called Text it is the text's. Also anglicised — the four per-side border composites say `Colour`.
	['color', 'Text colour']
];

// The rail's sector order, top to bottom, as author intents rather than CSS families. TEXT FIRST because a block is clicked to change what it SAYS far more often than to put a rule under it, and `Background & borders` LAST for the same reason.
export const SECTOR_ORDER: readonly string[] = ['typography', 'dimension', COLOUR_SECTOR];

// The one sector that ships OPEN. The preset ships its sectors shut and `Background & borders` is added shut too, which leaves a selected heading answered by closed bands and nothing to change — a question asked of an author who has already said what they want by clicking the thing. Text and no more: it is what a block was clicked to change, and one open band shows what the others are without opening them all, Spacing and Background carrying the accent dot when they hold a value. A DECLARED default, not a forced state — `style-panel.svelte` keeps the author's own collapse on top.
export const OPEN_SECTORS: readonly string[] = ['typography'];

// The only length units an email may offer. The preset gives every length all six of GrapesJS's units, and three of them are the same lie as `min-height`: Word supports none of the viewport- or root-relative ones, and inside a fixed 640px card they mean nothing even where they do resolve — an author who sets a font size in `vh` sees it in the canvas and the recipient sees the browser default. `em` STAYS, being relative to the element's own font size and how a line height is properly expressed. Applied as an INTERSECTION, never a replacement: a property offering only `px` keeps only `px`, and one offering none of these is left alone rather than given a unit it never had.
export const MAILABLE_LENGTH_UNITS: readonly string[] = ['px', '%', 'em'];

// Properties in the WRONG SECTION, moved: `[from, to, id]`. `vertical-align` is the only one. CSS files it with the inline box, which is why the preset has it under Text, but on a `<td>` — which is what every layout box here is — it says whether the content sits at the top, middle or bottom of the cell, and it is what stops a short email floating in the middle of the page (`PAGE_HTML` sets it for exactly that). A layout decision, so it belongs beside Height and Padding.
export const REHOMED_PROPERTIES: readonly (readonly [string, string, string])[] = [
	['typography', 'dimension', 'vertical-align']
];

// The order of the Text sector, the ONE sector an author always sees open. THE MEASURED CONTROLS FIRST, THE PICTURE CONTROLS AFTER — the reference inspector's own arrangement. Two things that separates: colour sits WITH size and line height, which is the whole reason `MOVED_COLOUR_PROPERTIES` leaves text colour in this sector, since an order putting `color` five rows below Font size would make that decision come to nothing; and font-weight / font-style / text-decoration / text-align are four icon-and-word controls in a row, which grouped read as one band of switches and interleaved with number fields read as a list with no shape. Anything the preset adds that is not named here keeps its place at the end, so an upgrade adds a control rather than losing one.
export const TYPOGRAPHY_ORDER: readonly string[] = [
	'font-family',
	'color',
	'font-size',
	'line-height',
	'letter-spacing',
	'font-weight',
	'font-style',
	'text-decoration',
	'text-align'
];

// NUMBERS, NOT NAMED STEPS. Both of these read as a measurement and were declared as selects: letter
// spacing offered Tight/Slight/Wide, weight offered Normal/Bold. A named step cannot express the value
// the design actually wants (the wordmark's 0.09em, a 600 button), and a select whose stored value is
// not among its options renders EMPTY over a real declaration, which reads as "not set". The DEFAULT is
// a number too, resolved in `property-control.svelte` — `normal px` was the same complaint one level up.
export const RETYPED_PROPERTIES: readonly (readonly [string, StylePropertyDef])[] = [
	[
		'typography',
		{
			property: 'letter-spacing',
			label: 'Letter spacing',
			type: 'number',
			default: 'normal',
			// `em` first: spacing that tracks the type size survives a font-size change, which px does not.
			units: ['em', 'px'],
			min: -1
		}
	],
	[
		'typography',
		{
			property: 'line-height',
			label: 'Line height',
			type: 'number',
			default: 'normal',
			// NO UNITS, WHICH IS THE WHOLE POINT: a line height is a RATIO, and the one property here
			// that must not carry one. GrapesJS derives it from letter spacing — `['line-height', {},
			// 'letter-spacing']` in its own preset — so it inherited a LENGTH's units, and `UnitInput`
			// substitutes the first offered unit for a value carrying none (`unit-input.svelte`:
			// `parts.unit ?? fallbackUnit ?? defaultUnit(units)`). A 1.8 the author typed committed as
			// `1.8px`, which collapses the line box in every inbox — and `1.8em` is no better, since it
			// resolves against the element's own size instead of being inherited as a ratio. Offering
			// none makes `defaultUnit` answer null and `joinDimension` pass the number through whole.
			// `normal` is CSS's own initial value; `property-control.svelte` shows it as 1.2.
			units: [],
			min: 0
		}
	],
	[
		'typography',
		{
			property: 'font-weight',
			label: 'Weight',
			// A DROPDOWN, unlike letter spacing: the useful weights are a named scale a typeface actually
			// ships, and a free number invites 450 from a font that has 400 and 500. Every value this
			// editor's own blocks declare IS an option (400, 600, 700) — a select whose stored value is
			// not among its options renders EMPTY over a real declaration, which reads as "not set".
			// Unsetting it is the `Default` option `property-control.svelte` puts at the top.
			type: 'select',
			default: '400',
			options: [
				{ id: '300', label: 'Light (300)' },
				{ id: '400', label: 'Regular (400)' },
				{ id: '500', label: 'Medium (500)' },
				{ id: '600', label: 'Semibold (600)' },
				{ id: '700', label: 'Bold (700)' },
				{ id: '800', label: 'Extrabold (800)' }
			]
		}
	]
];

export const ADDED_STYLE_PROPERTIES: readonly (readonly [string, StylePropertyDef])[] =
	BORDER_SIDES.map((side) => [COLOUR_SECTOR, borderSideProperty(side)] as const);

// TEXT STYLING IS FOR ELEMENTS THAT CAN HOLD TEXT. A property can be mailable, correctly labelled, correctly sectioned — and still mean NOTHING for what is selected. Nine rows describe an element's TEXT, and unguarded the rail offers all nine over an `<img>`, whose text does not exist: an author picks a colour, the panel lights the row, and nothing changes.
// THE RULE IS THE CONTENT MODEL, NOT A LIST OF OUR COMPONENTS. An HTML VOID element cannot contain a text node, so no text property can reach one — a closed set defined by the spec rather than by which blocks we ship, which is what stops a future block quietly needing an entry here. A `<td>` KEEPS its typography, including the case that looks like a counter-example: a cell holding only an image still styles text, `text-align: center` on it being how an email centres that image. An UNREADABLE subject keeps the whole rail, hiding a control on a guess leaving the rail merely missing something with nothing on screen to be suspicious of.
// APPLIED THROUGH GRAPESJS'S OWN HOOK: `Property.isVisible` may be a PREDICATE, checked on every target change and BEFORE every other visibility rule (:63394-63406); a sector whose every property answers false is itself marked invisible (:67751), and the panel reads `getSectors({ visible: true })`. So the whole Text sector disappears for an image with no list of sectors held anywhere in the view.

// The HTML void elements, in the spec's own set. `img` and `hr` are the two the email rail actually meets; the rest are here because the SET is the rule — trimming it to "the ones we ship" would turn a standards test back into a list of our own component names.
export const VOID_TAGS: readonly string[] = [
	'area',
	'base',
	'br',
	'col',
	'embed',
	'hr',
	'img',
	'input',
	'link',
	'meta',
	'source',
	'track',
	'wbr'
];

export interface TagBearer {
	get?(key: string): unknown;
}

// The one argument GrapesJS hands a property's `isVisible` predicate (grapes.mjs :63406). `component` is absent when the StyleManager is aimed at a rule rather than a selection, so a rule-only `target` is the fallback.
export interface StyleVisibility {
	component?: TagBearer | null;
	target?: TagBearer | null;
}

// Can this element hold text? A tag we cannot read answers YES — see above on hiding a control on a guess.
export function canHoldText(subject: TagBearer | null | undefined): boolean {
	const tag = subject?.get?.('tagName');
	if (typeof tag !== 'string' || tag === '') return true;
	return !VOID_TAGS.includes(tag.toLowerCase());
}

export function textPropertyVisible({ component, target }: StyleVisibility = {}): boolean {
	return canHoldText(component ?? target);
}

// VERTICAL ALIGNMENT IS A CELL'S PROPERTY. On a `<td>` it decides where short content sits in a tall
// cell — the whole reason `PAGE_HTML` sets it. On a block element it does NOTHING: a heading offered
// `baseline` and changing it changed nothing on the canvas and nothing in an inbox, which teaches an
// author that the rail lies. An UNREADABLE tag keeps the control, the same rule `canHoldText` follows.
const VERTICAL_ALIGN_TAGS: readonly string[] = ['td', 'th', 'img', 'span', 'a'];

export function verticalAlignVisible({ component, target }: StyleVisibility = {}): boolean {
	const tag = (component ?? target)?.get?.('tagName');
	if (typeof tag !== 'string' || tag === '') return true;
	return VERTICAL_ALIGN_TAGS.includes(tag.toLowerCase());
}

// THE WIDTH DIVERGENCE. A cell can state its width TWICE, as the legacy `width` attribute and in CSS. That is deliberate — Word reads the attribute — but it means the two can disagree, and the Style rail edits only one of them. So an author who changes a cell's width sees the canvas and the Preview honour it and ships an email that renders at the OLD width in Outlook: a lie in the one direction the author cannot check.
// No palette block ships a percentage other than 100%, and the sync still earns its place because the palette is not the only source of markup: four blocks ship `width="100%"` cells, a firm can load a shipped default, and the HTML view lets one paste a table in. It runs on `component:styleUpdate` — the event's real name in 0.21.13, `Component.__onStyleChange` triggering it at grapes.mjs :29020-29030, not the `component:update:style` an attribute change emits.
// THREE RULES, the third being the one that stops the fix breaking a legitimate idiom. (1) A PERCENTAGE attribute follows a PERCENTAGE CSS width, verbatim: no rounding, `33.33%` staying `33.33%`, a rounded attribute being a smaller version of the divergence being fixed. (2) It is REMOVED when the CSS width is anything else — leaving `width="50%"` next to `width:200px` IS the bug — and never TRANSLATED, the legacy grammar having no unit-bearing form, so `em`/`auto`/`calc()` have no attribute form at all and a rule translating px alone would be one nobody could predict. (3) NOTHING HAPPENS when the CSS declares no width: the browser then honours the presentational attribute, so the canvas already shows what Outlook will, and acting would be destructive — a `<table>` whose `width="100%"` is its only width shrinks to its content once stripped of it.
// DELIBERATELY OUT OF SCOPE: an attribute that is NOT a percentage. `width="600"` alongside `width:100%;max-width:600px` — the Email card's panel — is the fluid-hybrid idiom, where the two are MEANT to differ (fixed for Word, fluid-capped for everyone else), and "deliberately different" cannot be told from "drifted" without knowing the idiom. The accepted cost: editing that panel's Width does not reach its attribute.

// A width the legacy attribute can carry EXACTLY as CSS states it. Anchored, and no sign: a negative or unitless value is not a percentage, and `%` is the only unit the attribute shares with CSS.
const PERCENTAGE_WIDTH = /^\d+(?:\.\d+)?%$/;

// What the `width` ATTRIBUTE should become, given what it holds and what the CSS width now says: a string to write, `null` to remove it, `undefined` to leave it alone. Pure, so the decision table is assertable without an editor.
export function syncedWidthAttribute(
	attributeWidth: unknown,
	cssWidth: unknown
): string | null | undefined {
	const attr = typeof attributeWidth === 'string' ? attributeWidth.trim() : '';
	if (!PERCENTAGE_WIDTH.test(attr)) return undefined;
	const css = typeof cssWidth === 'string' ? cssWidth.trim() : '';
	if (!css) return undefined;
	if (!PERCENTAGE_WIDTH.test(css)) return null;
	// Rule 1 — and already-in-step is a no-op, which is what makes this idempotent. That matters beyond tidiness: every attribute write is a model change, so one that changed nothing would still land an undo entry, and the parse of a stored template fires this event for every element.
	return css === attr ? undefined : css;
}

// Keep one component's legacy `width` attribute in step with its CSS width — the `component:styleUpdate` handler. See above for the rules.
// READS THE COMPONENT, not just the event payload. `props.style` is the whole new style object today (grapes.mjs :26969-26992, :29372), but the name `newStyles` invites a future version to pass only the CHANGED keys — at which point a payload-only reader would stop self-healing a drifted attribute and would answer "no CSS width" (rule 3) for a component that has one.
// SKIPS UNCOMMITTED WRITES. `__p` is GrapesJS's own marker for a partial update (:26962-26965) — what the rail writes while a number field is being dragged — and those are `avoidStore`, so an attribute write beside one would be a lone undo entry reverting something invisible. Skipping costs nothing, a committed write always following (even the arrow-drag path re-sets non-partially on mouse-up, :45072-45081), and landing the attribute in that same callstack is what makes backbone-undo fuse the two into ONE step (:98-114), so the CSS and the attribute can never be undone apart.
export function syncWidthAttribute(
	component: any,
	props?: { style?: Record<string, unknown> }
): void {
	const attributes = component?.get?.('attributes');
	if (!attributes || typeof attributes !== 'object') return;
	const own = component?.getStyle?.();
	const style = (own ?? props?.style ?? {}) as Record<string, unknown>;
	if (style.__p || props?.style?.__p) return;
	const next = syncedWidthAttribute((attributes as Record<string, unknown>).width, style.width);
	if (next === undefined) return;
	// A copy, and never `addAttributes`: that merges over `getAttributes()`, which RECOMPUTES `style` into the attribute map (:30084-30096), freezing a snapshot of the current styling into `attributes.style` — which the `change:attributes` handler then feeds back through `setStyle` (:29265-29268). The raw prop never holds `style`, the parser having lifted it into its own model prop (:26646-26650), so a copy cannot loop.
	const updated: Record<string, unknown> = { ...attributes };
	if (next === null) delete updated.width;
	else updated.width = next;
	component.setAttributes?.(updated);
}

// THE CTA INK, STATED TWICE — the same class of problem as the width divergence, answered the same way. The button's label colour is declared on the anchor AND on the label span inside it, because no single one of them survives every client (see THE BULLETPROOF BUTTON). With two statements of one authored value the rail can edit one and leave the other behind, which is worse than `width="50%"` beside `width:30%`: which ink the recipient sees would depend on their client. So a `color` edit on either level is mirrored to the other, and an author never has to know the span is there.
// THE FLAG IS NORMALISED OUT on the way across. GrapesJS makes `!important` STICKY — `parseValue` (:55677-55688) lifts it into a separate `important` attribute a later plain colour never clears, and `getFullValue` (:55762-55772) re-appends it to every write — so once any flagged colour has been through the rail in a session, later colour edits carry the flag too. On this element that is the defect itself: an inline `!important` is what Word invalidates.

function bareColour(value: unknown): string {
	return typeof value === 'string' ? value.replace(/\s*!\s*important\s*$/i, '').trim() : '';
}

// The colour to write to the OTHER level of a button, given what the edited level now says and what the other currently says; `undefined` means leave it alone. Idempotent by comparing the BARE colours (any `!important` flag stripped), which is what stops the mirror ping-ponging: the write this returns fires `component:styleUpdate` on the partner, whose own pass then finds the two in agreement.
export function mirroredLabelInk(
	editedColour: unknown,
	partnerColour: unknown
): string | undefined {
	const next = bareColour(editedColour);
	if (!next) return undefined;
	return next === bareColour(partnerColour) ? undefined : next;
}

// The `<span>` a CTA anchor wraps its label in, if it still has one — identified STRUCTURALLY (an anchor's only child, a span) rather than by `data-gjs-name`, because the names are session-scoped and a REOPENED template carries none of them (see NAMES).
function labelSpan(anchor: any): any {
	const children = childList(anchor);
	const only = children.length === 1 ? children[0] : undefined;
	return String(only?.get?.('tagName') ?? '').toLowerCase() === 'span' ? only : undefined;
}

// Keep a CTA anchor's ink and its label span's ink in step — the second `component:styleUpdate` handler. Reads the component rather than the payload, skips partial (`__p`) writes and lands its own write in the same callstack, all three for the reasons `syncWidthAttribute` documents; the last is what fuses the pair into ONE undo step, so the two inks can never be undone apart into disagreement.
export function syncButtonLabelInk(
	component: any,
	props?: { style?: Record<string, unknown> }
): void {
	const own = (component?.getStyle?.() ?? props?.style ?? {}) as Record<string, unknown>;
	if (own.__p || props?.style?.__p) return;
	const tag = String(component?.get?.('tagName') ?? '').toLowerCase();
	const partner =
		tag === 'a'
			? labelSpan(component)
			: tag === 'span' && String(component.parent?.()?.get?.('tagName') ?? '').toLowerCase() === 'a'
				? component.parent()
				: undefined;
	if (!partner?.addStyle) return;
	const next = mirroredLabelInk(own.color, (partner.getStyle?.() ?? {}).color);
	if (next === undefined) return;
	// `addStyle`, not `setStyle`: it merges one property instead of replacing the whole declaration list, so the partner keeps its typography (the anchor) or gains nothing else (the span).
	partner.addStyle({ color: next });
}

// Bring the preset's style rail to what an email author needs: drop what a client will not honour, add what it will, gather the fills and borders into one sector, and keep the two values an email states twice — the legacy `width` attribute and the CTA's ink — in step with what the rail edits. ONE function for all of it: everything touching the StyleManager must run in the same `load`-time seam (see below), and a second lifecycle hook is a second thing to keep in step.
export function configureStyleRail(editor: any): void {
	const tune = (): void => {
		const sm = editor.StyleManager;
		if (!sm?.removeProperty) return;
		for (const [sector, property] of UNMAILABLE_STYLE_PROPERTIES) {
			// removeProperty is a no-op for an id the preset did not register, so an upgrade that renames or drops one of these degrades to "nothing to remove" rather than throwing.
			sm.removeProperty(sector, property);
		}
		// The preset's sector names, in the author's words — THROUGH I18N, because the model alone is not enough. This is the `-sub` label trap `borderSideProperty` documents, one level up: `Sector.getName()` consults `i18n['styleManager.sectors.<id>']` FIRST and GrapesJS's en locale has an entry for every sector it ships, so `set('name', ...)` succeeds, `get('name')` answers the new string, and `getName()` — what the panel and its tests read — still answers "Dimension" (verified live). So the message is overridden AND the model set; both are idempotent, so the double run is free.
		editor.I18n?.addMessages?.({
			en: {
				styleManager: {
					sectors: Object.fromEntries(SECTOR_RENAMES),
					properties: Object.fromEntries(PROPERTY_RENAMES)
				}
			}
		});
		for (const [id, name] of SECTOR_RENAMES) sm.getSector?.(id)?.set?.('name', name);
		for (const [id, name] of PROPERTY_RENAMES)
			sm.getProperty?.('typography', id)?.set?.('name', name);
		// `addSector` returns the existing sector for an id it already holds, so the double run is free. APPENDED, not `at: 0`: this sector holds a background and five border composites, so putting fifteen rows of border above the typography pushes the common case below the fold. `open: false` likewise.
		sm.addSector?.(COLOUR_SECTOR, { name: COLOUR_SECTOR_LABEL, open: false, properties: [] });
		const colourProps = sm.getProperties?.(COLOUR_SECTOR);
		for (const [from, id, relabel] of MOVED_COLOUR_PROPERTIES) {
			// Idempotency guard, and it has to come first: on a second run the property is no longer in its source sector, so an unguarded move would fall through to the `extend` fallback and add a SECOND copy.
			if (sm.getProperty?.(COLOUR_SECTOR, id)) continue;
			// `removeProperty` returns the undestroyed model (grapes.mjs :59918), so this MOVES the preset's own property rather than re-declaring it: labels, units, types and sub-properties all survive, and the `-sub` i18n collision documented on `borderSideProperty` is never in play.
			const moved = sm.removeProperty?.(from, id);
			if (!moved) {
				// The preset renamed or dropped it. The background control is the point of the sector, so rebuild it from GrapesJS's own built-in definition (`extend` is `Sector.checkExtend`, :58807-58815) rather than leaving the sector short. No fallback for `border`: its absence costs the four sides their adjacency, not their existence.
				if (relabel) sm.addProperty?.(COLOUR_SECTOR, { extend: id, name: relabel });
				continue;
			}
			if (relabel) moved.set?.('name', relabel);
			if (id === BORDER_ANCHOR)
				moved.getProperty?.('border-width')?.set?.('default', BORDER_WIDTH_DEFAULT);
			colourProps?.add?.(moved);
		}
		// APPENDED, the adjacency coming from the declared order rather than an index search: `border` is LAST in MOVED_COLOUR_PROPERTIES exactly so that appending its four per-edge forms behind it puts them where they belong. THE RAIL'S ORDER is then applied once every sector it names exists; a sector is a model in a plain collection, so moving one is a remove-then-add at an index, guarded at every step so a preset that renames or drops a sector leaves the order as it found it rather than throwing.
		const sectors = sm.getSectors?.();
		if (sectors?.remove && sectors.add) {
			SECTOR_ORDER.forEach((id, index) => {
				const sector = sm.getSector?.(id);
				if (!sector) return;
				sectors.remove(sector, { silent: true });
				sectors.add(sector, { at: index, silent: true });
			});
		}
		for (const [sector, property] of ADDED_STYLE_PROPERTIES) {
			// Idempotent, where the prune is idempotent for free: removing an id twice is a no-op, adding one twice is a SECOND control in the rail. `tune` can run twice (see the two calls below).
			if (sm.getProperty?.(sector, property.property)) continue;
			sm.addProperty?.(sector, property);
		}
		// FOLD the last sector away, after the sides so the border family stays in order. Same remove-then-add, with the same guard first: on a second run the property is already here and re-adding would give the rail two Corner radius controls.
		for (const [id, relabel] of FOLDED_PROPERTIES) {
			if (sm.getProperty?.(COLOUR_SECTOR, id)) continue;
			const moved = sm.removeProperty?.(FOLDED_SECTOR, id);
			if (!moved) continue;
			if (relabel) moved.set?.('name', relabel);
			colourProps?.add?.(moved);
		}
		// And the heading itself, once it is empty: an empty sector still draws its own collapsible header, a section that opens onto nothing, which reads as a control that failed to load. ONLY ON A COUNT WE CAN ACTUALLY READ — `=== 0` rather than `(... ?? 0) === 0`, because an undefined length means "this collection did not tell us", and not knowing what is in a sector can never be grounds for deleting it.
		if (sm.getProperties?.(FOLDED_SECTOR)?.length === 0) sm.removeSector?.(FOLDED_SECTOR);

		// UNITS an email client will not honour, taken off every length in the rail — see `MAILABLE_LENGTH_UNITS`. Depth-bounded and recursive, because the units of a composite live on its sub-properties and not on the composite. Idempotent for free: a second run intersects an already-intersected list.
		const narrowUnits = (property: any, depth: number): void => {
			if (!property || depth > 3) return;
			const units = property.get?.('units');
			if (Array.isArray(units) && units.length > 0) {
				const kept = units.filter((unit: string) => MAILABLE_LENGTH_UNITS.includes(unit));
				// Only when something is left AND something goes: an empty result would leave a number field with no unit to write, which is worse than offering one Outlook ignores.
				if (kept.length > 0 && kept.length < units.length) property.set?.('units', kept);
			}
			for (const sub of property.getProperties?.() ?? []) narrowUnits(sub, depth + 1);
		};
		// `map(s => s)` rather than a spread: `getSectors` answers a Backbone collection, which is iterable only through its own methods.
		for (const sector of sm.getSectors?.()?.map?.((one: any) => one) ?? [])
			for (const property of sector.getProperties?.() ?? []) narrowUnits(property, 0);

		for (const [from, to, id] of REHOMED_PROPERTIES) {
			if (sm.getProperty?.(to, id)) continue;
			const moved = sm.removeProperty?.(from, id);
			if (moved) sm.getProperties?.(to)?.add?.(moved);
		}
		// RETYPED PROPERTIES — see `RETYPED_PROPERTIES`. Guarded on the type, so the double `tune` run
		// does not destroy and rebuild the control an author may be holding open.
		// ALWAYS REPLACED, never skipped on a matching type. The guard here used to `continue` when the
		// held property already had the type we declare — which silently discarded the whole declaration
		// for any property whose type we were NOT changing: `font-weight` is already a `select`, so our
		// numeric options never landed and the rail kept GrapesJS's own Thin/Extra-Light/Semi-Bold set.
		// The type was never the point; the options, units, min and default are. Remove-then-add is
		// idempotent by construction, which is what the guard was protecting (`tune` runs twice).
		for (const [sector, property] of RETYPED_PROPERTIES) {
			sm.removeProperty?.(sector, property.property);
			sm.addProperty?.(sector, property);
		}
		// And the order WITHIN the one sector that is always open — see `TYPOGRAPHY_ORDER`. Guarded on the collection actually offering both, so a GrapesJS change that reshapes it costs the order, not the rail.
		const typo = sm.getProperties?.('typography');
		if (typo?.remove && typo.add) {
			// Reverse, inserting each at the front: that way the list needs no arithmetic about how many of its predecessors were present, and a property the preset does not have is simply skipped.
			for (const id of [...TYPOGRAPHY_ORDER].reverse()) {
				const property = sm.getProperty?.('typography', id);
				if (!property) continue;
				typo.remove(property);
				typo.add(property, { at: 0 });
			}
		}
		// TEXT PROPERTIES ONLY WHERE THERE CAN BE TEXT — see the note above `VOID_TAGS`. Every property in the sector, not a named list: the sector IS the text sector, so a control a preset adds to it is answered by the same rule. Last of the property passes, so it reaches the sector as it finally stands.
		for (const property of sm.getProperties?.('typography')?.map?.((one: any) => one) ?? [])
			property.set?.('isVisible', textPropertyVisible);
		// AND VERTICAL ALIGNMENT ONLY ON A CELL — see `VERTICAL_ALIGN_TAGS`. It lives in the layout
		// sector (`MOVED_PROPERTIES`), so the text rule above does not reach it.
		sm.getProperty?.('dimension', 'vertical-align')?.set?.('isVisible', verticalAlignVisible);
		// WHICH BANDS START OPEN — see `OPEN_SECTORS`. Last in `tune`, after every sector exists and the empty one has gone: run earlier it would reach the colour sector before `addSector` created it. Applied to every sector in the order rather than only the listed ones, so a preset shipping one open cannot leave two.
		for (const id of SECTOR_ORDER) sm.getSector?.(id)?.set?.('open', OPEN_SECTORS.includes(id));
	};
	// OUTSIDE `tune`, and deliberately: this is an event subscription and nothing resets the editor's event bus, where `tune` runs twice by design (see below) — so a subscription inside it would leave two handlers writing the attribute on one edit. Registered here rather than in `createEmailEditor` because it is the other half of the same contract: the Width control is only honest if the attribute follows it, and the Text colour control only honest on a CTA if both levels of its ink follow it.
	editor.on?.('component:styleUpdate', syncWidthAttribute);
	editor.on?.('component:styleUpdate', syncButtonLabelInk);
	// AFTER `load`, not straight after init. When `grapesjs.init` returns, the StyleManager holds GRAPESJS'S OWN default sectors: the preset installs its email-tuned set from an `editor.onReady` callback that does `sectors.reset()` then `sectors.add(...)`, and the reset discards whatever was there. So a prune or an add called inline is applied to a sector set about to be thrown away — the pruned properties are simply still in the rail, and a lost addition is harder to notice, a rail merely missing a control with nothing on screen to be suspicious of.
	// Our handler runs after the preset's because both land on `em.on('load', ...)` and plugins are initialised inside `init`, so the preset registered first; the immediate call covers the case where `load` has already fired, and the adds above tolerate the double run that implies.
	editor.on('load', tune);
	tune();
}

// UNDO is owned outright here, because of two traps neither of which is visible from the outside: a healthy stack and a registered keymap sit quite happily above an `undo()` that returns normally and does nothing at all.
// TRAP 1 — GrapesJS gates BOTH doors on rich-text-editing state: `undo`/`redo` are `!em.isEditing() && um.undo(all)` (grapes.mjs 0.21.13 :54221, :54243) and `Keymaps.add` computes `ableTorun = !em.isEditing() && !editor.Canvas.isInputFocused()` (:51724). So while `em.isEditing()` is truthy, `undo()` is a NO-OP that returns `this` and the `core:undo` keymap never fires — nothing logged, nothing thrown. It does not consume the stack, but neither does a WORKING undo: backbone-undo moves `stack.pointer` and never pops (:54290, :54371), so a constant `getStack().length` is evidence of nothing and `getPointer()` is the one to read. And `em.isEditing()` is set BY OUR OWN PALETTE: `text` and `heading` declare `activate: true`, so `__endDrag` does `first.trigger('active')` (:39247-39256), `ComponentTextView.onActive` enables the RTE and its last act is `em.setEditing(this)` (:35850), and the flag then stays set until a `mousedown` lands somewhere. So ungated, DROPPING A HEADING KILLS UNDO UNTIL YOU HAPPEN TO CLICK SOMETHING. The TRACKING is fine, which is how to tell the two apart: `UndoManager.getInstance().undo(true)` bypasses the gate.
// TWO HYPOTHESES RULED OUT, so nobody re-tests them. It is NOT `loadEmailContent` leaving the UndoManager watching a dead collection (`setComponents` clears and re-adds the SAME collection, :36955-36972, and registration is per-component and dynamic anyway, :28991-28995), and it is NOT the canvas iframe swallowing the keystroke (`FrameView.renderBody` re-dispatches every `keydown` onto the iframe element in the parent document, :31253-31263). Both verified live. So the editing session is ENDED before undoing, which needs no guesswork about whether the author typed anything: `disableEditing` already answers that (:35686-35729) — with nothing typed the `content !== lastContent` guard skips `syncContent`, so no entry is created and the undo reverts THE DROP; with text typed the exit commits it as ONE entry. That is coarser than the browser's per-keystroke undo inside a contentEditable, and the trade is deliberate: native undo mutates the frame DOM without touching the model (which catches up only on blur), so interleaving the two stacks is how a document and its history come apart. We `preventDefault` for the same reason.
// TRAP 2 — GRAPESJS'S OWN KEYMAPS CANNOT CARRY ANY OF THIS. Re-registering `core:undo`/`core:redo` with `{ prevent: true, force: true }` is correct on its own terms, works in isolation, and still fails in the running app with every introspectable field looking right. The reason is a global: `Keymaps.removeAll` ends with `keymaster.handlers = {}` (:51781-51787), and keymaster is a module-level singleton bound once to `window.document` (:51663), so that map is shared by EVERY GrapesJS editor on the page. `Keymaps.destroy` calls `removeAll` and `Editor.destroy` calls that (:51788, :62526) — so destroying any editor silently unbinds every other live editor's keystrokes. In dev that happens on every HMR remount: `createEmailEditor` is awaited, so the new editor finishes installing before the outgoing component's cleanup runs `destroy()`, which wipes the new bindings.
// INTROSPECTION LIES ABOUT IT. Reproduced live with two editors on one page: after `A.destroy()`, B still reports `Keymaps.getAll()['core:undo']` present, 9 keymaps and `typeof handler === 'function'`, while `keymaster.handlers` is `{}` and nothing fires. `Keymaps.add` stores only `{ id, keys, handler }` (:51716) and NEVER keeps `opts`, so a missing `opts` proves nothing about whose registration is in force; the one discriminator is `typeof handler` — a FUNCTION is ours, the STRING `'core:undo'` GrapesJS's. SO: GrapesJS's two keymaps are REMOVED and one `keydown` listener of our own carries the chord. Nothing another editor does can unbind it, and it needs no `force` flag because it was never gated. One parent-document listener covers the canvas too, by the re-dispatch noted above.

// The GrapesJS keymaps we take over, so its own gated handlers can never fire alongside ours and undo twice from one keystroke. Ids and chords are GrapesJS's verbatim — `Keymaps.remove` unbinds by `keys.split(', ')`, so a mis-spelled chord silently leaves the binding in place.
export const SUPERSEDED_KEYMAPS: readonly { id: string; keys: string }[] = [
	{ id: 'core:undo', keys: '⌘+z, ctrl+z' },
	{ id: 'core:redo', keys: '⌘+shift+z, ctrl+shift+z' }
];

// UndoManager options passed to `grapesjs.init`. `trackSelection: false` (GrapesJS defaults it ON) keeps SELECTION changes off the stack: with it on, `postLoad` registers `em.get('selected')` (grapes.mjs :54149-54151) and every canvas click becomes an undo entry, so an author who clicks two blocks and presses ctrl+z watches nothing happen — verified live, four `Selected` entries, one undo consuming all four, the exported HTML byte-identical. What is lost is undoing a deletion re-selecting the block.
export const UNDO_MANAGER_CONFIG = { trackSelection: false } as const;

// End any live rich-text-editing session, resolving once GrapesJS has committed the typed content to the model (or established there was none). Returns whether there was a session at all. Awaiting matters: `disableEditing` is async — it awaits the RTE teardown, re-reads the content, then `syncContent`s it into the model (grapes.mjs :35686-35729) — so an undo fired before that settles would run against a model that has not heard about the author's last sentence, which would then land AFTER the undo.
export async function endTextEditing(editor: any): Promise<boolean> {
	const em = editor?.getModel?.();
	// `em.get('editing')` is deliberately the state `em.isEditing()` itself reads, so this cannot go out of step with the gate it exists to clear. It holds the component's VIEW; `getEditing()` is GrapesJS's normaliser to the MODEL, which carries `.view` — accept either, so a future version storing the model here degrades to working rather than to silently doing nothing.
	const editing = em?.get?.('editing');
	if (!editing) return false;
	const view = editing.disableEditing ? editing : (editing.view ?? editor?.getEditing?.()?.view);
	if (typeof view?.disableEditing === 'function') {
		await view.disableEditing();
	} else {
		// Degraded path: clear the flag so undo is reachable at all. This forfeits the content sync, so an in-flight text edit is lost — which is why it is the fallback and not the mechanism.
		em?.setEditing?.(false);
	}
	return true;
}

// Undo the last `count` changes from any state — including with a caret sitting in a text block, which is where a block drop leaves it. ONE implementation for the keystroke, the toolbar button and the history menu, so the three cannot drift; `endTextEditing` runs once rather than per step, since after the first it is a no-op anyway and repeating it would be a second chance to commit something mid-rewind. A step is a FUSED GROUP — `um.undo(all = true)` is magic mode — which is why one press undoes a whole block drop and why the history menu's counts line up with its rows.
export async function undoEmailEdits(editor: any, count = 1): Promise<void> {
	await endTextEditing(editor);
	for (let n = 0; n < count; n += 1) editor?.UndoManager?.undo?.();
}

export async function undoEmailEdit(editor: any): Promise<void> {
	await undoEmailEdits(editor, 1);
}

/** Redo, mirroring undoEmailEdit — `redo()` carries the identical `!em.isEditing()` gate. */
export async function redoEmailEdit(editor: any): Promise<void> {
	await endTextEditing(editor);
	editor?.UndoManager?.redo?.();
}

// The author-facing name of a component: the block's own `data-gjs-name` where it has one, else the end of GrapesJS's `getName()` chain (which our `i18n.domComponents.names` override re-words — see NAMES). Title-cased, GrapesJS's own answers being lowercase type ids. Shared by the Style rail heading and the undo-history menu, so a block cannot be a 'Button fill' in one and a 'Table cell' in the other.
export function componentDisplayName(component: any): string {
	const raw =
		component?.get?.('custom-name') ||
		component?.getName?.() ||
		component?.get?.('type') ||
		component?.get?.('tagName') ||
		'';
	const name = String(raw).replace(/[-_]/g, ' ').trim();
	return name ? name.charAt(0).toUpperCase() + name.slice(1) : 'Component';
}

export interface UndoGroup {
	index: number;
	actions: { type?: string; before?: any; after?: any; object?: any }[];
}

// Label one step of the history, in the author's words. DELIBERATELY NOT the document editor's `labelForGroup`, which this otherwise mirrors. Two things about it do not survive contact with the email stack, both checked against real entries. It prints `group.labels.join(', ')`, and those come from the tracked options' `action` key, which GrapesJS fills with its own event slugs — a real drop here produced `["component:add"]`, so the menu would read "component:add", the leak of internal vocabulary the NAMES override closes. And its fallbacks test `'style' in after` / `'attributes' in after`, but a `change` entry's `after` is `object.toJSON()`, the WHOLE component, so every change carries every key: measured, a colour edit and a link-href edit produced identical key sets and both would read "Style change".
// So the verb is only ever taken from something observable, everything but add/remove being the neutral "Edited". A finer verb IS derivable by diffing `style`, and is deliberately not: with `avoidInlineStyle: false` a style edit also rewrites the `style` ATTRIBUTE, so the differ would need to special-case that leak and would then mislabel silently the day the flag changes.
export function describeUndoGroup(group: UndoGroup): string {
	const actions = group?.actions ?? [];
	const adds = actions.filter((a) => a.type === 'add');
	const removes = actions.filter((a) => a.type === 'remove');
	// An add AND a remove in one fused step is a component that MOVED — GrapesJS re-inserts rather than relocating. COUNTED, not paired, because a corrected drop (see WHERE A DROP LANDS) carries a remove too: the sorter's add, then our remove and re-add into the cell. Identity cannot separate the two cases, every action naming the same component, but the arithmetic can — a move removes exactly what it adds, a relocated drop adds one MORE. Without this a freshly dropped block read "Moved Text".
	const added = adds[0];
	const removed = removes[0];
	if (added && removed && adds.length <= removes.length)
		return `Moved ${componentDisplayName(added.after)}`;
	if (added) return `Added ${componentDisplayName(added.after)}`;
	if (removed) return `Removed ${componentDisplayName(removed.before)}`;
	const changed = actions.find((a) => a.type === 'change');
	if (changed) return `Edited ${componentDisplayName(changed.object)}`;
	return 'Edit';
}

// The undoable steps, newest first — what the history menu lists. Only groups at or below the pointer have been APPLIED; anything above it has been undone and belongs to redo, and filtering on that is what keeps "Undo 3 actions" honest after a few undos.
export function undoHistory(editor: any): { label: string }[] {
	const um = editor?.UndoManager;
	if (!um?.getGroupedStack) return [];
	const pointer = um.getPointer?.() ?? -1;
	return (um.getGroupedStack() as UndoGroup[])
		.filter((g) => g.index <= pointer)
		.reverse()
		.map((g) => ({ label: describeUndoGroup(g) }));
}

// Elements whose own undo belongs to the browser: the inspector's Destination / Alt text fields and the style rail's inputs. Same rule keymaster applies (`filter`, grapes.mjs :51469-51473) — and it must NOT extend to the canvas, whose contentEditable is exactly who needs our undo.
const NATIVE_UNDO_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

// Whether a keydown is the undo/redo chord, and which. `null` for anything else. Reads `key` with a `keyCode` fallback because the event arriving from the canvas is a CLONE: `FrameView` re-dispatches it through `createCustomEvent`, which re-attaches `keyCode`/`which` by `defineProperty` but constructs the clone from a `KeyboardEventInit` (grapes.mjs :20664-20686). `metaKey` is ⌘ on macOS and `ctrlKey` elsewhere, which is what GrapesJS's `'⌘+z, ctrl+z'` expressed; `altKey` disqualifies, alt+ctrl+z being undo nowhere and claiming it swallowing AltGr.
export function matchesUndoChord(ev: {
	key?: string;
	keyCode?: number;
	ctrlKey?: boolean;
	metaKey?: boolean;
	shiftKey?: boolean;
	altKey?: boolean;
	target?: unknown;
}): 'undo' | 'redo' | null {
	if (ev.altKey || !(ev.ctrlKey || ev.metaKey)) return null;
	const isZ = ev.key === 'z' || ev.key === 'Z' || ev.keyCode === 90;
	if (!isZ) return null;
	const tag = (ev.target as { tagName?: string } | null)?.tagName;
	if (tag && NATIVE_UNDO_TAGS.has(tag)) return null;
	return ev.shiftKey ? 'redo' : 'undo';
}

// Own ctrl+z / ⌘+z (and the shifted redo) outright: drop GrapesJS's two keymaps and carry the chord on a listener of our own. Returns a disposer — the listener outlives the editor otherwise, and a stale one would undo into a destroyed instance. `doc` is injectable so the matching, the guard and the disposal are testable without a DOM. Read-only is handled by NOT CALLING THIS — see `createEmailEditor`.
export function installUndoShortcuts(
	editor: any,
	doc: Pick<Document, 'addEventListener' | 'removeEventListener'> = document
): () => void {
	const dropKeymaps = (): void => {
		// Idempotent: `remove` is a no-op for an id it does not hold. Called again on `load` only as cheap insurance — GrapesJS registers its defaults inside `init`, so the synchronous call already suffices.
		for (const { id } of SUPERSEDED_KEYMAPS) editor?.Keymaps?.remove?.(id);
	};
	dropKeymaps();
	editor?.on?.('load', dropKeymaps);

	let disposed = false;
	const onKeydown = (ev: KeyboardEvent): void => {
		if (disposed) return;
		const action = matchesUndoChord(ev);
		if (!action) return;
		// During an HMR remount both the outgoing and incoming editors are briefly alive, both with a listener on the same document. The outgoing one's canvas is already detached, so requiring a connected container keeps one keystroke from being undone twice, in two editors.
		const container = editor?.getContainer?.();
		if (container && !container.isConnected) return;
		// We are the only handler now, so the browser's native contentEditable undo must be suppressed or a single press undoes in two places at once.
		ev.preventDefault();
		void (action === 'undo' ? undoEmailEdit(editor) : redoEmailEdit(editor)).catch(() => {});
	};
	// Capture phase: the canvas re-dispatches its keydown onto the iframe element, and a bubbling listener would be racing anything in the app that stops propagation on the way up (the portal's ⌘K bar).
	doc.addEventListener('keydown', onKeydown, true);

	return () => {
		disposed = true;
		doc.removeEventListener('keydown', onKeydown, true);
		editor?.off?.('load', dropKeymaps);
	};
}

export async function createEmailEditor(opts: CreateEmailEditorOpts): Promise<any> {
	ensureEmailEditorStyles();

	const [gjsMod, presetMod] = await Promise.all([
		import('grapesjs'),
		import('grapesjs-preset-newsletter')
	]);
	// grapesjs is an OBJECT (it has `.init`); the preset is a FUNCTION plugin. CJS<->ESM interop nests the real value under `.default` — or `.default.default` — depending on how the dep was bundled and whether Vite pre-optimised it, so a bare `{ default: ... }` destructure gave "presetNewsletter is not a function" on a route where the module was not optimised the same way.
	const gm = gjsMod as any;
	const grapesjs = typeof gm.default?.init === 'function' ? gm.default : gm;
	const pm = presetMod as any;
	const presetNewsletter = [pm.default, pm.default?.default, pm].find(
		(c: unknown) => typeof c === 'function'
	);
	if (typeof presetNewsletter !== 'function') {
		throw new Error('grapesjs-preset-newsletter did not resolve to a plugin function');
	}

	const editor = grapesjs.init({
		container: opts.container,
		height: '100%',
		width: 'auto',
		storageManager: false, // manual save only — the page owns save/publish
		// Keep authored styles INLINE (email clients need them, and it stops GrapesJS stripping the loaded default's inline styles on parse). BOTH flags are required, and `avoidInlineStyle` alone is a trap: with `forceClass` left at its default `true`, GrapesJS still lifts a component's inline style into a generated CLASS rule (a probe `<div style="color:red">` exported as `<div class="c1853">`), so DUPLICATING a block produced a clone with no rule of its own — naked in the preview and in the real send, while the canvas still looked right.
		avoidInlineStyle: false,
		forceClass: false,
		panels: { defaults: [] },
		undoManager: { ...UNDO_MANAGER_CONFIG },
		// THE TWO MANAGERS WHOSE UI IS OURS. `custom: true` means "avoid rendering the default UI" (grapesjs 0.23.4 `dist/index.d.ts`) and NOTHING MORE — every sector and property MODEL is still built, so all the email-safety configuration here and in `configureStyleRail` still applies. That is the whole safety argument for the rework; `style-panel/read.ts` sets it out. What renders instead is `style-panel/style-panel.svelte`, our own controls over the property models — the DOM it replaces needed ~156 lines of `!important` CSS and still left native selects, a native colour input and Spectrum's popover, none of which docs/design-language.md permits.
		// THE PALETTE IS DELIBERATELY NOT ONE OF THEM: it is GrapesJS's own, rendered as-is. Its block drag is not a thing to reimplement — `BlockView` owns the HTML5 handlers, the temporary drop model, the sorter hand-off and the teardown, and driving any of that from outside gets a drag that starts and never places, or never starts. So the icon rail is CHROME: its flyout is the mount point, what renders inside is GrapesJS's own block elements styled compact by `gjs-email-blocks` above.
		// `custom: true` so GrapesJS renders NO asset manager. Its own modal is unstyled chrome — it was carrying 17 `!important` restyles here and still showed its own header, dashed drop zone and teal button. The choice lives in the style rail instead (`ImageSource`), and `asset:custom` merely shuts the shell GrapesJS still opens (email-editor.svelte).
		assetManager: { custom: true },
		blockManager: {
			appendTo: opts.blocksEl,
			appendOnClick: (block: any) => appendBlockOnClick(editor, block)
		},
		// No layerManager/traitManager mount either: both rails were dropped (see email-editor.svelte). With no `appendTo` and `panels.defaults` empty, GrapesJS builds them but renders them nowhere. The selector manager is `custom` rather than unmounted because there is no container of ours to mount it into, and an `appendTo` pointing at nothing is how a manager renders itself into `document.body`.
		selectorManager: { custom: true },
		styleManager: { custom: true },
		// THE DURABLE HALF OF NAMES. `data-gjs-name` is consumed at parse and never re-exported, so it names what THIS session dropped and nothing else: reopen a saved template and every re-parsed element falls through to `i18n[type]`, which is where 'Table cell' comes from — untouched, an author's own template describes itself in HTML table vocabulary while a freshly-dropped block describes itself properly. Overriding that fallback vocabulary answers the reopened case for every element at once, without putting a marker in what the recipient receives; it is a rename, not a mechanism, so `data-gjs-name` still wins where present. `tbody` is in the list because DOMParser inserts one into every `<table><tr>`, so it is already in the export and reachable by the select-parent arrow, where it reads 'Table body'.
		i18n: { messages: { en: { domComponents: { names: COMPONENT_NAMES } } } },
		// Fixed email widths — the frame IS the device, centred by the layout CSS. Names must match the preset's set-device commands ('Desktop' / 'Mobile portrait').
		deviceManager: { devices: EMAIL_DEVICES.map((d) => ({ ...d })) },
		// EDITOR CHROME OUT OF THE EMAIL. GrapesJS's `protectedCss` defaults to `'* { box-sizing: border-box; } body {margin: 0;}'` (grapes.mjs :24308) and `getCss()` PREPENDS it unless the caller passes `avoidProtected` (:61313). Every call site here does pass it — but a `*` rule that reaches the saved CSS is inlined onto every element of the delivered email by `to_email_html`, i.e. `box-sizing: border-box` on every `<td>`, `<a>` and `<p>` a recipient receives (observed in a real inbox). One forgotten option at one future call site is all it takes, so the string is removed outright. Its declarations are MOVED, not dropped: `canvasCss` is injected into the canvas frame's own `<style>` beside `protectedCss` (:31230) and is NOT part of `getCss()`, so no authored style renders differently.
		protectedCss: '',
		canvasCss: '* { box-sizing: border-box; } body { margin: 0; }',
		// Canvas-only stylesheet and script, neither part of `getCss()`/`getHtml()`. THE SCRIPT MAKES CONTROL FLOW VISIBLE: an author typing `{% if brand.support_email %}` into a text block would otherwise see logic that looks like copy, in the one place the difference matters, so it draws `{% %}` as a code chip and `{{ }}` as a token pill. It is an OVERLAY and never touches the canvas DOM, which is not a style preference — `getHtml()` returns that DOM and that string is what is saved and then emailed, so wrapping matched text in spans would ship the decoration inside the email. CACHE-BUSTED IN DEV, these being `static/` files loaded by the canvas IFRAME rather than modules Vite tracks, so nothing invalidates them.
		canvas: {
			styles: [canvasAsset('/gjs-email-canvas.css')],
			scripts: [canvasAsset('/gjs-email-jinja-hl.js')]
		},
		plugins: [
			(e: any) =>
				presetNewsletter(e, {
					useCustomTheme: false,
					// The managers are `custom: true`, so they have no panel views to show — the preset must not try to toggle them on load or on selection.
					showStylesOnChange: false,
					updateStyleManager: true,
					// NONE of the preset's blocks — the rail is ours (see EMAIL_BLOCKS). Its `addBlock` registers only ids present in this list, so an empty list leaves the style sectors and the CSS-inline command, the two things we load it for, untouched.
					blocks: []
				})
		]
	} as any);

	// The `brand-logo` type BEFORE the palette and before any content is loaded: `isComponent` is consulted while parsing, so a type registered afterwards would not recognise the masthead in a template that is already open — it would parse as four loose siblings that one session treats as a component and the next does not.
	registerBrandLogo(editor);
	registerChoice(editor);
	registerEmailField(editor);
	registerImageActivation(editor);
	registerEmailImage(editor);
	// AFTER the generic one, which it extends.
	registerMastheadImage(editor);
	registerEmailBlocks(editor);
	configureStyleRail(editor);

	// The newsletter preset replaces the entire panel set with its own top/side chrome. We own the chrome via our own rails, so drop those panel bars — the style sectors and commands it registered live in separate modules and survive.
	editor.Panels?.getPanels?.()?.reset?.([]);

	editor
		.getWrapper?.()
		?.set?.({ selectable: false, hoverable: false, highlightable: false, layerable: false });

	// MUTUALLY EXCLUSIVE, not merely ordered. Installing the undo path unconditionally and relying on `applyReadOnly`'s `Keymaps.removeAll()` to strip it again would work, but reordering two adjacent lines would then silently hand an `email_templates:read`-only principal a working undo. `applyReadOnly` keeps its own `removeAll()` for GrapesJS's remaining keymaps.
	if (opts.readOnly) {
		applyReadOnly(editor);
	} else {
		// Drops land where the pointer is, inside a cell — see WHERE A DROP LANDS. BOTH halves live in this branch rather than being neutralised afterwards: `applyReadOnly` does clear `droppable` per INSTANCE, which beats the widened type defaults, so ordering these before it would also work — the same correct-but-fragile arrangement the read-only branch avoids.
		widenDropTargets(editor);
		installDropCorrection(editor);
		installPaletteDropFix(editor);
		// Stashed on the editor rather than returned: `createEmailEditor` answers with the editor, and a disposer dropped on the floor leaves a listener undoing into a destroyed instance. The component calls this in its cleanup, beside the frame-height disposer.
		(editor as any).__disposeUndoShortcuts = installUndoShortcuts(editor);
	}

	return editor;
}
