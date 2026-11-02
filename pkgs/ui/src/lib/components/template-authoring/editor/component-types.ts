/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Custom GrapesJS component types — ported (near-verbatim) from the reference
 * editor (platform-1 `template-editor.tsx`). These are pure GrapesJS API calls;
 * the only adaptation is reading the variable schema through a `getSchema()`
 * accessor instead of a React ref, so the `jinja-var` traits stay current.
 */
import type { VarSchema } from './types';
// Per-type style-sector gating: these build the `unstylable` lists that hide whole
// sectors (GrapesJS auto-hides a sector once all its properties are unstylable). See
// style-config.ts for why this needs no bespoke mechanism.
import { showOnlySectors, hideSectors, sectorPropsExcept } from './style-config';
// The canvas draws the REAL chart, by the same renderer the drawer's preview and the worker use.
import { boundFields, previewCompFromAttrs, renderChartSvg } from './chart-preview';
import { formatGBP } from '../../../money';

const TEXT_TAGS = new Set(['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'P', 'UL', 'OL', 'BLOCKQUOTE']);

// ── Jinja table (doc-jtable) helpers ─────────────────────────────────────────
// A jinja-driven table: header row(s) + a repeating body row wrapped in {% for %}.
// The editor shows the loop body once (jinja statement tags stripped) with the
// {{ row.field }} expressions visible; serialisation (toHTML) emits the full template.
export interface JColumn {
	header: string;
	expr: string;
	/** A CSS percentage without the sign, e.g. `40`. Blank or absent means automatic.
	 *
	 *  PERCENTAGES, not pixels, for the reason `tables.py::_widths` gives: the table is 100% of
	 *  whatever box it sits in, and a page's content width is a physical measurement an author
	 *  cannot see from the editor. Stored per column and round-tripped through the template's own
	 *  `<colgroup>`, so the authored HTML remains the single source of truth for this table. */
	width?: string;
	/** `left` (the default, and never written) | `right` | `center`. A column of figures is
	 *  comparable at a glance only when its digits line up. */
	align?: string;
}

export function defaultJColumns(): JColumn[] {
	return [
		{ header: 'Column', expr: 'row.field_1' },
		{ header: 'Value', expr: 'row.field_2' }
	];
}

const escapeHtml = (s: string): string =>
	s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const stripTags = (s: string): string => s.replace(/<[^>]*>/g, '');

/** Build the full jinja/HTML table template from a dataset name + columns. Styling lives in
    the `.doc-jtable-inner` class (canvas CSS + exported stylesheet), so the template stays a
    clean, readable {% for %} loop rather than inline-styled cells. */
/** A dataset code as the words a reader knows it by: drop the catalogue's reserved `book_` prefix
    and turn underscores into spaces (`book_valuations` → `valuations`). Mirrors
    `app/render/empty_state.py::label_for_code` — the same table can be emptied in the editor
    preview and in the PDF, and two phrasings for one fact reads as two faults. */
export function datasetLabel(dataset: string): string {
	const words = (dataset || '')
		.trim()
		.replace(/^book_/, '')
		.replace(/_/g, ' ')
		.trim();
	return words || 'data';
}

/** A per-column width as CSS, or "" for automatic. Clamped 1-100 and never rescaled to total 100:
 *  an author's numbers are their numbers (`tables.py::_widths` states the same rule). */
function jColumnWidth(raw: string | undefined): string {
	const num = Number.parseFloat(String(raw ?? ''));
	if (!Number.isFinite(num) || num <= 0) return '';
	return `${Math.max(1, Math.min(100, num))}%`;
}

/** `left` is the default and is NOT written, so it cannot fight an authored stylesheet. */
function jColumnAlign(raw: string | undefined): string {
	return raw === 'right' || raw === 'center' ? ` style="text-align:${raw}"` : '';
}

export function buildJTemplate(dataset: string, columns: JColumn[]): string {
	const ds = (dataset || 'items').trim();
	const cols = columns.length ? columns : defaultJColumns();
	const widths = cols.map((c) => jColumnWidth(c.width));
	// `table-layout:fixed` ONLY when a width is set. Under `auto` the engine treats a declared width
	// as a suggestion and widens the column to its longest cell anyway — which is why a width can
	// appear to do nothing — but with none set, `auto` sizing columns to their content is the better
	// layout and must not be imposed on every table that already exists.
	const anyWidth = widths.some(Boolean);
	const tableStyle = anyWidth ? ' style="table-layout:fixed"' : '';
	// A `<colgroup>`, which is also how the width ROUND-TRIPS: the authored HTML stays the single
	// source of truth for this table, so `parseJTemplate` reads the widths back out of it rather
	// than needing a parallel store that could disagree with the markup.
	const group = anyWidth
		? `<colgroup>${widths.map((w) => (w ? `<col style="width:${w}"/>` : '<col/>')).join('')}</colgroup>`
		: '';
	const head = `<thead><tr>${cols
		.map((c) => `<th${jColumnAlign(c.align)}>${escapeHtml(c.header)}</th>`)
		.join('')}</tr></thead>`;
	const bodyRow = `<tr>${cols
		.map((c) => `<td${jColumnAlign(c.align)}>{{ ${c.expr.trim()} }}</td>`)
		.join('')}</tr>`;
	// An `{% else %}` row, so an empty dataset renders a sentence instead of a header floating over
	// blank space. Without it every author's table showed its headings and nothing beneath when the
	// client had none of that thing — indistinguishable from a broken binding, and the single most
	// common way a generated document looked wrong.
	//
	// It carries NO `{{ … }}`: `parseJTemplate` extracts columns by finding `<td>{{ expr }}</td>`
	// inside the loop, so a token here would be read as an extra column and the round trip would
	// silently classify the author's table as custom.
	const emptyRow =
		`<tr class="jt-empty"><td colspan="${cols.length}">` +
		`${escapeHtml(`No ${datasetLabel(ds)} to show`)}</td></tr>`;
	const body = `<tbody>{% for row in ${ds} %}${bodyRow}{% else %}${emptyRow}{% endfor %}</tbody>`;
	return `<table class="doc-jtable-inner"${tableStyle}>${group}${head}${body}</table>`;
}

/** Editor view of a template: drop jinja statement tags so the loop body shows once.

    The `{% else %}` BRANCH goes with them — content included, not just its tags. Stripping tags
    alone would leave the empty row beside the data row, so the author would see a phantom
    "No … to show" row in their table that is not a row they can edit or delete. */
export function jtemplateToEditorHtml(template: string): string {
	return template
		.replace(/\{%\s*else\s*%\}[\s\S]*?(?=\{%\s*endfor\s*%\})/gi, '')
		.replace(/\{%[^%]*?%\}/g, '');
}

/** Map a dataset's stored sample (`{columns, rows}`) onto the table's columns for the CANVAS
    preview: each column's `row.<field>` expression reads its value from the sample row. Filters
    aren't evaluated (the canvas is illustrative) — the base field is taken straight from the
    sample; a column whose field the sample lacks renders blank. */
export function jRowsFromSample(
	sample: { columns: string[]; rows: unknown[][] },
	columns: JColumn[]
): string[][] {
	const idx: Record<string, number> = {};
	sample.columns.forEach((c, i) => (idx[String(c)] = i));
	const baseField = (expr: string): string =>
		expr
			.replace(/\|[\s\S]*$/, '')
			.trim()
			.replace(/^row\./, '')
			.trim();
	return sample.rows.map((r) =>
		columns.map((col) => {
			const i = idx[baseField(col.expr)];
			const v = i != null && Array.isArray(r) && i < r.length ? r[i] : undefined;
			return v == null ? '' : String(v);
		})
	);
}

// ── Table design (jdesign) — visual styling layered over the clean template ──────
// Kept OUT of the template (which stays a clean {% for %} loop for the raw editor) and
// stored as a separate prop. It is applied only when rendering the canvas + serialising
// to HTML, as CSS custom properties + modifier classes on the <table> tag that the
// `.doc-jtable-inner` rules consume (canvas CSS + exported stylesheet).
export interface JDesign {
	headerBg: string;
	headerFg: string;
	/** Body-row background (`transparent` = the page shows through). */
	rowBg: string;
	/** Body text colour. */
	bodyFg: string;
	borderColor: string;
	borders: 'all' | 'rows' | 'none';
	striped: boolean;
	stripeColor: string;
	/** Body font size in px. */
	fontSize: number;
	align: 'left' | 'center' | 'right';
}

export function defaultJDesign(): JDesign {
	return {
		headerBg: '#f1f5f9',
		headerFg: '#1e293b',
		rowBg: 'transparent',
		bodyFg: '#374151',
		borderColor: '#e2e8f0',
		borders: 'all',
		striped: false,
		stripeColor: '#f8fafc',
		fontSize: 13,
		align: 'left'
	};
}

/** Coerce an unknown (parsed JSON) value into a complete JDesign, filling gaps with defaults. */
export function normaliseJDesign(v: unknown): JDesign {
	const d = defaultJDesign();
	if (!v || typeof v !== 'object') return d;
	const o = v as Record<string, unknown>;
	if (typeof o.headerBg === 'string') d.headerBg = o.headerBg;
	if (typeof o.headerFg === 'string') d.headerFg = o.headerFg;
	if (typeof o.rowBg === 'string') d.rowBg = o.rowBg;
	if (typeof o.bodyFg === 'string') d.bodyFg = o.bodyFg;
	if (typeof o.borderColor === 'string') d.borderColor = o.borderColor;
	if (o.borders === 'all' || o.borders === 'rows' || o.borders === 'none') d.borders = o.borders;
	if (typeof o.striped === 'boolean') d.striped = o.striped;
	if (typeof o.stripeColor === 'string') d.stripeColor = o.stripeColor;
	if (typeof o.fontSize === 'number' && o.fontSize > 0) d.fontSize = o.fontSize;
	if (o.align === 'left' || o.align === 'center' || o.align === 'right') d.align = o.align;
	return d;
}

/** class list + inline CSS-var style for the <table> from a design. */
export function jdesignTableAttrs(d: JDesign): { cls: string; style: string } {
	const classes = ['doc-jtable-inner'];
	if (d.striped) classes.push('jt-striped');
	if (d.borders === 'none') classes.push('jt-border-none');
	else if (d.borders === 'rows') classes.push('jt-border-rows');
	const style = [
		`--jt-header-bg:${d.headerBg}`,
		`--jt-header-fg:${d.headerFg}`,
		`--jt-row-bg:${d.rowBg}`,
		`--jt-body-fg:${d.bodyFg}`,
		`--jt-border:${d.borderColor}`,
		`--jt-stripe:${d.stripeColor}`,
		`--jt-font:${d.fontSize}px`,
		`--jt-align:${d.align}`
	].join(';');
	return { cls: classes.join(' '), style };
}

/** Inject the design's class + CSS vars into the template's opening <table> tag. The
    template itself stays design-free; design is applied at render/serialise time only. */
export function applyJDesign(html: string, d: JDesign): string {
	const { cls, style } = jdesignTableAttrs(d);
	// THE FIXED LAYOUT HAS TO SURVIVE THIS, and it did not. The whole opening tag is REPLACED to
	// bake the design in, so the `table-layout:fixed` that `buildJTemplate` writes was discarded
	// on every export AND every canvas render — and under `auto` a declared column width is only
	// a suggestion the engine overrides to fit the longest cell. So the per-column widths were
	// stored correctly, round-tripped correctly, and did nothing at all.
	//
	// DERIVED FROM THE MARKUP, never copied out of the tag being replaced: the widths live in the
	// `<colgroup>`, so reading the need for a fixed layout from there cannot disagree with them —
	// where copying a string would keep `fixed` on a table whose last width had just been cleared.
	const fixed = /<col[^>]*width:/i.test(html) ? ';table-layout:fixed' : '';
	return html.replace(/<table\b[^>]*>/i, `<table class="${cls}" style="${style}${fixed}">`);
}

// ── Sidebar table preview ────────────────────────────────────────────────────
// A faithful mini-preview for the editor's right rail. Reuses the exact `.doc-jtable-inner` +
// `--jt-*` contract (jdesignTableAttrs + DOC_JTABLE_CSS), so header colours (incl. transparent),
// borders, striping, alignment and font size all track the live design — canvas == preview == PDF.
const SAMPLE_LABELS = ['Global Equity', 'Corporate Bond', 'Property Fund', 'Cash', 'Index Tracker'];

/** Illustrative display strings for a data-table preview: one row per index, one cell per column
    base field (`row.x`). Filters aren't evaluated client-side, so these are sample values only. */
export function sampleTableRows(bases: string[], n = 3): string[][] {
	const cell = (base: string, i: number): string => {
		const b = base.replace(/^row\./, '').toLowerCase();
		if (/(value|amount|balance|total|cost|price)/.test(b)) {
			return formatGBP(10000 * (n - i));
		}
		if (/(pct|percent|alloc|rate|weight|return|yield)/.test(b)) return (100 / n).toFixed(1) + '%';
		if (/(name|fund|product|label|category|asset|holding)/.test(b)) {
			return SAMPLE_LABELS[i % SAMPLE_LABELS.length]!;
		}
		return 'Sample ' + (i + 1);
	};
	return Array.from({ length: n }, (_, i) => bases.map((base) => cell(base, i)));
}

/** A table with no columns has nothing to draw, and a bare empty box reads as broken. */
const EMPTY_TABLE_NOTE =
	`<div style="display:flex;align-items:center;justify-content:center;width:100%;` +
	`font-family:Arial,sans-serif;font-size:10px;color:#94a3b8">` +
	`Choose columns to show this table</div>`;

const escHtml = (s: string): string =>
	String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c] ?? c);

/** A design-styled `<table>` for the sidebar preview — headers + body rows of display strings.
    Pair with DOC_JTABLE_CSS so the `.doc-jtable-inner` rules resolve the `--jt-*` variables. */
/** `cols` carries per-column width and alignment, and mirrors `tables.py` — the same `<colgroup>`,
 *  the same `table-layout: fixed` only when a width is set, the same left default. Optional, so the
 *  jtable preview (which has no per-column settings) renders exactly as it did. */
export interface PreviewColumn {
	/** A CSS percentage, e.g. `40%`. Blank or absent means automatic. */
	width?: string;
	align?: string;
}

export function tablePreviewInner(
	design: JDesign,
	headers: string[],
	rows: string[][],
	cols?: readonly PreviewColumn[]
): string {
	const { cls, style } = jdesignTableAttrs(design);
	const at = (i: number): PreviewColumn => cols?.[i] ?? {};
	const align = (i: number): string => {
		const a = at(i).align;
		return a === 'right' || a === 'center' ? `text-align:${a};` : '';
	};
	// The attribute is OMITTED when there is nothing to put in it, so a table with no per-column
	// settings is byte-identical to what this produced before they existed — which is what the
	// jtable preview renders, and what its own tests assert on. An empty `style=""` is also a
	// declaration an authored stylesheet would then have to fight.
	const attr = (css: string): string => (css ? ` style="${css}"` : '');
	// Only when a width is actually set: under `auto` the engine treats a declared width as a
	// suggestion and widens the column to its longest cell anyway, which is why a width appeared to
	// do nothing — but with none set, `auto` is the better layout and must not be overridden.
	const anyWidth = (cols ?? []).some((c) => !!c.width);
	const group = anyWidth
		? `<colgroup>${headers
				.map((_, i) => (at(i).width ? `<col style="width:${escHtml(at(i).width!)}"/>` : '<col/>'))
				.join('')}</colgroup>`
		: '';
	const tableStyle = anyWidth ? `${style};table-layout:fixed` : style;
	const head = headers.map((h, i) => `<th${attr(align(i))}>${escHtml(h)}</th>`).join('');
	// `word-wrap` only under a fixed layout, where a column will not widen for a long value and an
	// unbroken string (an ISIN, a reference) would otherwise spill out of its cell.
	const cellCss = (i: number): string => align(i) + (anyWidth ? 'word-wrap:break-word;' : '');
	const body = rows
		.map(
			(r) =>
				`<tr>${headers
					.map((_, i) => `<td${attr(cellCss(i))}>${escHtml(r[i] ?? '')}</td>`)
					.join('')}</tr>`
		)
		.join('');
	return (
		`<table class="${cls}" style="${tableStyle}">${group}` +
		`<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
	);
}

// ── Text table (doc-texttable) — a static, typed grid ────────────────────────
// Unlike the Jinja data table, this is plain content the author types directly: real
// <th>/<td> cells marked data-gjs-type=text (RTE-editable, like the Label+Value block).
// It carries no dataset or {% for %} loop, so the render pipeline passes it straight
// through — it's just a styled HTML table. Shares the JDesign styling (`.doc-jtable-inner`
// + `--jt-*` vars) so text and data tables look identical.

/** Build the initial <table> HTML for a text table: a header row + `rows` body rows of
    `cols` editable cells, styled with the given design. */
export function buildTextTableHtml(rows: number, cols: number, d: JDesign): string {
	const { cls, style } = jdesignTableAttrs(d);
	const nCols = Math.max(1, cols);
	const nRows = Math.max(1, rows);
	const head = Array.from(
		{ length: nCols },
		(_, i) => `<th data-gjs-type="text">Heading ${i + 1}</th>`
	).join('');
	const bodyCell = '<td data-gjs-type="text">Text</td>';
	const body = Array.from({ length: nRows }, () => `<tr>${bodyCell.repeat(nCols)}</tr>`).join('');
	return `<table class="${cls}" style="${style}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

/** A GrapesJS component DEFINITION for one editable table cell. Rows/columns are added by
    appending these objects — never HTML strings: a bare `<tr>`/`<td>`/`<th>` fragment is
    foster-parented out of its (absent) `<table>` by the browser's HTML parser, so an
    `append('<tr>…')` silently drops the markup. Component definitions bypass that parser. */
function ttCellDef(tag: 'td' | 'th', text: string): Record<string, unknown> {
	return { tagName: tag, type: 'text', content: text, attributes: { 'data-gjs-type': 'text' } };
}

/** Print stylesheet for Jinja tables — the `.doc-jtable-inner` rules that turn the per-table
    `--jt-*` variables (set by jdesignTableAttrs) into real header colours / borders / striping /
    alignment. These must ship INSIDE the exported document CSS: the render worker only sees the
    saved html + css, and `editor.getCss()` does NOT include the canvas stylesheet these rules
    normally live in. Kept in sync with `static/grapesjs-canvas.css` (the canvas copy). */
export const DOC_JTABLE_CSS = [
	'.doc-jtable-inner{width:100%;border-collapse:collapse;font-size:var(--jt-font,13px);' +
		'color:var(--jt-body-fg,#374151);line-height:1.45;}',
	'.doc-jtable-inner th,.doc-jtable-inner td{border:1px solid var(--jt-border,#e2e8f0);' +
		'padding:7px 10px;text-align:var(--jt-align,left);vertical-align:top;}',
	'.doc-jtable-inner td{background:var(--jt-row-bg,transparent);}',
	'.doc-jtable-inner th{background:var(--jt-header-bg,#f1f5f9);color:var(--jt-header-fg,#1e293b);' +
		'font-weight:600;border-bottom:2px solid var(--jt-border,#e2e8f0);}',
	'.doc-jtable-inner.jt-border-rows th,.doc-jtable-inner.jt-border-rows td{border-left:0;border-right:0;}',
	'.doc-jtable-inner.jt-border-none th,.doc-jtable-inner.jt-border-none td{border:0;}',
	'.doc-jtable-inner.jt-striped tbody tr:nth-child(even) td{background:var(--jt-stripe,#f8fafc);}',
	// The `{% else %}` row: quiet and centred, so an empty table reads as an answer rather than as
	// a table that failed to load.
	'.doc-jtable-inner tr.jt-empty td{text-align:center;color:#94a3b8;font-style:italic;}'
].join('\n');

/** Parse a generated-shape template back to {dataset, columns}; null if custom/unrecognised. */
export function parseJTemplate(template: string): { dataset: string; columns: JColumn[] } | null {
	try {
		const ds = template.match(/\{%\s*for\s+row\s+in\s+([\w.]+)\s*%\}/i);
		if (!ds) return null;
		const thead = template.match(/<thead>[\s\S]*?<tr>([\s\S]*?)<\/tr>[\s\S]*?<\/thead>/i);
		const headers = thead
			? [...thead[1]!.matchAll(/<th[^>]*>([\s\S]*?)<\/th>/gi)].map((m) => stripTags(m[1]!).trim())
			: [];
		// Terminates on `else` OR `endfor`. The capture is lazy, so with an `{% else %}` branch
		// present it would otherwise run past the data row and swallow the empty one — which today
		// happens to extract the right columns only because the empty row carries no `{{ … }}`.
		// That is luck, not a contract, and the moment a token appears in the empty row the parse
		// returns null and the author's table is treated as custom.
		const bodyRow = template.match(
			/\{%\s*for[^%]*%\}\s*<tr>([\s\S]*?)<\/tr>\s*\{%\s*(?:else|endfor)\s*%\}/i
		);
		const exprs = bodyRow
			? [...bodyRow[1]!.matchAll(/<td[^>]*>\s*\{\{\s*([\s\S]*?)\s*\}\}\s*<\/td>/gi)].map((m) =>
					m[1]!.trim()
				)
			: [];
		if (!headers.length || headers.length !== exprs.length) return null;
		// The width and the alignment come back out of the markup that carries them. Without this the
		// dialog rebuilds the template from its column list and every per-column setting is lost the
		// next time anything else about the table is edited.
		const group = template.match(/<colgroup>([\s\S]*?)<\/colgroup>/i);
		const widths = group
			? [...group[1]!.matchAll(/<col[^>]*>/gi)].map(
					(m) => /width:\s*([\d.]+)%/i.exec(m[0])?.[1] ?? ''
				)
			: [];
		const aligns = thead
			? [...thead[1]!.matchAll(/<th[^>]*>/gi)].map(
					(m) => /text-align:\s*(right|center)/i.exec(m[0])?.[1]?.toLowerCase() ?? ''
				)
			: [];
		return {
			dataset: ds[1]!,
			columns: headers.map((h, i) => ({
				header: h,
				expr: exprs[i]!,
				width: widths[i] ?? '',
				align: aligns[i] ?? ''
			}))
		};
	} catch {
		return null;
	}
}

// ── Jinja value expression = a base (variable/field) + a pipeline of filters ──────
// Shared by the table columns and the text variable picker so filters can be added /
// removed as chips anywhere a jinja value is edited. (Naive top-level `|` split — a
// filter arg containing a literal pipe is not supported, which is fine in practice.)
export function splitExpr(expr: string): { base: string; filters: string[] } {
	const parts = (expr ?? '').split('|').map((s) => s.trim());
	return { base: parts[0] ?? '', filters: parts.slice(1).filter(Boolean) };
}
export function joinExpr(base: string, filters: string[]): string {
	const b = (base ?? '').trim();
	const fs = filters.map((f) => f.trim()).filter(Boolean);
	return fs.length ? `${b} | ${fs.join(' | ')}` : b;
}

/** Curated common jinja filters (label + snippet appended after a pipe). Every snippet must name
    either a filter `apps/api/app/render/jinja_engine.py` registers — spelled EXACTLY as it does —
    or a Jinja builtin: anything else is a TemplateAssertionError when the document renders, days
    after it looked fine on the canvas, so `component-types.test.ts` reads the engine's source and
    fails on a name it cannot resolve.
    The builtins here (`int`/`abs`/`round`/`upper`/`title`/`capitalize`/`default`) do not share the
    registered filters' null handling — `upper` on a null field prints "NONE", `abs`/`round` raise,
    and `default` fires on Undefined only, so it leaves a present-but-null field blank rather than
    showing its placeholder. The engine's own `fallback` filter is the one that answers "what if
    there is nothing".
    `group` marks mutually-exclusive filters — only one per group can apply (e.g. you can't both
    `currency` and `percentage`, or `upper` and `title`). Ungrouped filters combine freely. */
export interface JinjaFilter {
	label: string;
	snippet: string;
	group?: 'number' | 'case';
}
export const JINJA_FILTERS: JinjaFilter[] = [
	{ label: 'Currency', snippet: 'currency', group: 'number' },
	{ label: 'Percentage', snippet: 'percentage', group: 'number' },
	{ label: 'Whole number', snippet: 'int', group: 'number' },
	{ label: 'Thousands separator', snippet: 'number', group: 'number' },
	{ label: 'Round (2 dp)', snippet: 'round(2)' },
	{ label: 'Absolute value', snippet: 'abs' },
	{ label: 'Date (01 Jan 2026)', snippet: "date('%d %b %Y')" },
	{ label: 'Uppercase', snippet: 'upper', group: 'case' },
	{ label: 'Lowercase', snippet: 'lower', group: 'case' },
	{ label: 'Title Case', snippet: 'title', group: 'case' },
	{ label: 'Capitalise', snippet: 'capitalize', group: 'case' },
	// `fallback`, not Jinja's `default`: the engine's `_finalize` maps a defined null to '' at output,
	// and `default` fires on Undefined ONLY — so an empty field took the dash's place with no dash in
	// it, which is the one case the label promises. `fallback` is the registered filter that answers
	// for a null, and it is what the email editor's Format control offers for the same reason.
	{ label: 'Fallback if empty (—)', snippet: "fallback('—')" }
];

const filterBase = (s: string): string => s.split('(')[0]!.trim();
const filterGroup = (snippet: string): JinjaFilter['group'] =>
	JINJA_FILTERS.find((f) => filterBase(f.snippet) === filterBase(snippet))?.group;

/** Filters still addable given the applied ones: excludes exact duplicates and any filter
    whose mutually-exclusive group already has a member applied. */
export function availableFilters(applied: string[]): JinjaFilter[] {
	const names = applied.map(filterBase);
	const groups = new Set(applied.map(filterGroup).filter(Boolean));
	return JINJA_FILTERS.filter(
		(f) => !names.includes(filterBase(f.snippet)) && !(f.group && groups.has(f.group))
	);
}
export function canAddFilter(applied: string[], snippet: string): boolean {
	return availableFilters(applied).some((f) => filterBase(f.snippet) === filterBase(snippet));
}
/** Addable filters split into labelled groups (Number / Text case / Other) for a picker
    with visual separation. Empty groups are dropped. */
export function filterOptgroups(applied: string[]): { label: string; items: JinjaFilter[] }[] {
	const avail = availableFilters(applied);
	const defs: { label: string; key: JinjaFilter['group'] }[] = [
		{ label: 'Number', key: 'number' },
		{ label: 'Text case', key: 'case' },
		{ label: 'Other', key: undefined }
	];
	return defs
		.map((d) => ({ label: d.label, items: avail.filter((f) => f.group === d.key) }))
		.filter((g) => g.items.length);
}

// ── Layer panel icons ────────────────────────────────────────────────────────
const ico = (body: string): string =>
	`<svg width="13" height="13" viewBox="0 0 13 13" fill="none" xmlns="http://www.w3.org/2000/svg">${body}</svg>`;

const LAYER_ICONS = {
	text: ico(
		`<line x1="1.5" y1="3.5" x2="11.5" y2="3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>` +
			`<line x1="1.5" y1="6.5" x2="9.5" y2="6.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>` +
			`<line x1="1.5" y1="9.5" x2="7" y2="9.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`
	),
	image: ico(
		`<rect x="1" y="2" width="11" height="9" rx="1" stroke="currentColor" stroke-width="1.2"/>` +
			`<circle cx="4" cy="5" r="1" fill="currentColor"/>` +
			`<path d="M1 9 L4.5 6 L7 8.5 L9 7 L12 9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>`
	),
	divider: ico(
		`<line x1="1" y1="6.5" x2="12" y2="6.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>`
	),
	callout: ico(
		`<rect x="1" y="2" width="11" height="9" rx="1" stroke="currentColor" stroke-width="1.2"/>` +
			`<rect x="1" y="2" width="2.5" height="9" rx="0.5" fill="currentColor"/>`
	),
	warning: ico(
		`<path d="M6.5 1.5 L12 11 H1 Z" stroke="currentColor" stroke-width="1.2" fill="none" stroke-linejoin="round"/>` +
			`<line x1="6.5" y1="4.8" x2="6.5" y2="8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<circle cx="6.5" cy="9.6" r="0.6" fill="currentColor"/>`
	),
	field: ico(
		`<rect x="1" y="3.5" width="4" height="2" rx="0.5" fill="currentColor"/>` +
			`<rect x="6.5" y="3.5" width="5.5" height="2" rx="0.5" fill="currentColor" opacity="0.5"/>` +
			`<rect x="1" y="7.5" width="4" height="2" rx="0.5" fill="currentColor"/>` +
			`<rect x="6.5" y="7.5" width="5.5" height="2" rx="0.5" fill="currentColor" opacity="0.5"/>`
	),
	col: ico(
		`<rect x="1" y="2" width="4.5" height="9" rx="0.5" stroke="currentColor" stroke-width="1.2"/>` +
			`<rect x="7.5" y="2" width="4.5" height="9" rx="0.5" stroke="currentColor" stroke-width="1.2"/>`
	),
	// Matches the lucide `file` icon used in the Pages list (TemplateEditor.svelte).
	// fill="none" is set per-path (not just on <svg>) because the vendor rule
	// `.gjs-layer__icon svg{fill:currentColor}` would otherwise render the closed
	// paths solid — that rule targets svg, so a path-level attribute wins.
	page:
		`<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">` +
		`<path fill="none" d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>` +
		`<path fill="none" d="M14 2v4a2 2 0 0 0 2 2h4"/>` +
		`</svg>`,
	section: ico(
		`<rect x="1" y="1.5" width="11" height="4" rx="0.5" stroke="currentColor" stroke-width="1.2"/>` +
			`<rect x="1" y="7.5" width="11" height="4" rx="0.5" stroke="currentColor" stroke-width="1.2"/>`
	),
	jinjaVar: ico(
		`<path d="M4,1.5 C2.5,1.5 2,2 2,3.5 L2,5 C2,6.5 1,7 0.5,6.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M4,11.5 C2.5,11.5 2,11 2,9.5 L2,8 C2,6.5 1,6 0.5,6.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M9,1.5 C10.5,1.5 11,2 11,3.5 L11,5 C11,6.5 12,7 12.5,6.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M9,11.5 C10.5,11.5 11,11 11,9.5 L11,8 C11,6.5 12,6 12.5,6.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>`
	),
	chart: ico(
		`<line x1="1.5" y1="11.5" x2="11.5" y2="11.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<rect x="1.5" y="7" width="2.5" height="4.5" fill="currentColor" rx="0.3"/>` +
			`<rect x="5.25" y="4" width="2.5" height="7.5" fill="currentColor" rx="0.3"/>` +
			`<rect x="9" y="6" width="2.5" height="5.5" fill="currentColor" rx="0.3"/>`
	),
	table: ico(
		`<rect x="1" y="2" width="11" height="9" rx="0.5" stroke="currentColor" stroke-width="1.2"/>` +
			`<line x1="1" y1="5.5" x2="12" y2="5.5" stroke="currentColor" stroke-width="1.2"/>` +
			`<line x1="6.5" y1="2" x2="6.5" y2="11" stroke="currentColor" stroke-width="1.2"/>`
	),
	repeat: ico(
		`<path d="M2,8 L2,4 C2,3 2.7,2 3.7,2 L10,2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M8.5,0.5 L10,2 L8.5,3.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>` +
			`<path d="M11,5 L11,9 C11,10 10.3,11 9.3,11 L3,11" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M4.5,9.5 L3,11 L4.5,12.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>`
	),
	conditional: ico(
		`<path d="M6.5,1 L6.5,4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M6.5,4 L3,8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M6.5,4 L10,8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<line x1="1.5" y1="11" x2="4.5" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>` +
			`<line x1="8.5" y1="11" x2="11.5" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`
	),
	condBranch: ico(
		`<line x1="6.5" y1="1.5" x2="6.5" y2="11.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<path d="M6.5,6.5 L11,6.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>`
	),
	header: ico(
		`<rect x="1" y="1.5" width="11" height="3.5" rx="0.5" stroke="currentColor" stroke-width="1.2" fill="currentColor" fill-opacity="0.2"/>` +
			`<line x1="1.5" y1="7.5" x2="11.5" y2="7.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<line x1="1.5" y1="10" x2="8" y2="10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>`
	),
	footer: ico(
		`<line x1="1.5" y1="3" x2="11.5" y2="3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<line x1="1.5" y1="5.5" x2="8" y2="5.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>` +
			`<rect x="1" y="8" width="11" height="3.5" rx="0.5" stroke="currentColor" stroke-width="1.2" fill="currentColor" fill-opacity="0.2"/>`
	)
};

/** The strip under a chart/table in the CANVAS: what this component is and what it is bound to.
 *
 * Editor chrome, never exported — the PDF gets the `<g-chart>` marker and the server-rendered
 * visual, so nothing here reaches a client.
 *
 * It reads as metadata rather than as a printed caption. A filled grey band with a solid rule
 * inside the white card looked like part of the document: the author could not tell the binding
 * summary from a figure caption they had written. So the band is gone — a hairline over the card's
 * own white, an uppercase eyebrow for the type, and the bindings as soft pills, which is the
 * vocabulary the rest of the editor's chrome already uses.
 *
 * Inline styles, not classes: this HTML is injected into the GrapesJS canvas iframe, which does not
 * carry the app's stylesheet (only `static/grapesjs-canvas.css`, which is for DOCUMENT styling —
 * putting editor chrome in there would leak it into what an author is styling).
 */
export function chartConfigCaption(attrs: Record<string, string>): string {
	const type = attrs['data-comp-type'] ?? 'line_chart';
	const isTable = type === 'data_table';
	const TYPE_LABELS: Record<string, string> = {
		line_chart: 'Line chart',
		bar_chart: 'Bar chart',
		pie_chart: 'Pie chart',
		donut_chart: 'Donut chart',
		stacked_area_chart: 'Stacked area chart',
		data_table: 'Data table'
	};
	const typeLabel = TYPE_LABELS[type] ?? 'Chart';
	const title = (attrs['data-comp-title'] ?? '').trim();
	const source = (attrs['data-comp-data-source'] ?? '').trim();

	// Manual (inline) rows are an alternative to a dataset source — don't report "no data source"
	// when the component carries its own rows.
	let manualRows = 0;
	try {
		const inline = JSON.parse(attrs['data-comp-inline-data'] || '{}');
		if (inline && Array.isArray(inline.rows)) manualRows = inline.rows.length;
	} catch {
		/* ignore malformed inline data */
	}
	const hasData = !!source || manualRows > 0;

	/** A soft pill. `mono` for a dataset code, which is an identifier the author typed. */
	const pill = (text: string, mono = false): string =>
		`<span style="display:inline-block;padding:1px 6px;border-radius:9999px;` +
		`background:#f1f5f9;color:#475569;font-size:9.5px;line-height:1.6;` +
		`${mono ? 'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' : ''}">` +
		`${escapeHtml(text)}</span>`;

	/** A field mapping: the axis as a quiet label, the field as the value. `—` when unset, so an
	    unfinished binding is visible rather than an absent pill. */
	const mapping = (axis: string, v?: string): string =>
		`<span style="font-size:9.5px;color:#64748b">` +
		`<span style="color:#94a3b8">${escapeHtml(axis)}</span> ${escapeHtml(v?.trim() || '—')}` +
		`</span>`;

	const bits: string[] = [];
	if (hasData) {
		bits.push(
			source ? pill(source, true) : pill(`Manual · ${manualRows} row${manualRows === 1 ? '' : 's'}`)
		);
		if (isTable) {
			const cols = (attrs['data-comp-columns'] ?? '').trim();
			if (cols) bits.push(mapping('Columns', cols));
		} else if (type === 'pie_chart' || type === 'donut_chart') {
			bits.push(mapping('Label', attrs['data-comp-label-field']));
			bits.push(mapping('Value', attrs['data-comp-value-field']));
		} else if (type === 'stacked_area_chart') {
			bits.push(mapping('X', attrs['data-comp-x-field']));
			bits.push(mapping('Series', attrs['data-comp-y-field']));
		} else {
			bits.push(mapping('X', attrs['data-comp-x-field']));
			bits.push(mapping('Y', attrs['data-comp-y-field']));
		}
	}

	// THE CARD SAYS WHAT IT IS. Floating over the corner of a drawing, an unlabelled panel of chips
	// reads as part of the picture — something the reader of the finished document might expect to
	// see — rather than as the editor telling an author how this component is wired. It is the one
	// heading here: the TYPE below it is a fact about the chart, like the dataset and the fields, so
	// it no longer competes as a second uppercase line.
	const heading =
		`<div style="font-size:8.5px;font-weight:700;letter-spacing:0.07em;text-transform:uppercase;` +
		`color:#94a3b8;padding-bottom:3px;margin-bottom:3px;border-bottom:1px solid rgba(15,23,42,0.08)">` +
		`${isTable ? 'Table details' : 'Chart details'}</div>`;
	const eyebrow = `<span style="font-size:9px;font-weight:600;color:#475569">${escapeHtml(typeLabel)}</span>`;
	// The author's own title, when there is one — the line that matters, so it takes the weight.
	const titleLine = title
		? `<div style="font-size:11.5px;font-weight:600;color:#334155;margin-top:1px">` +
			`${escapeHtml(title)}</div>`
		: '';

	// THE PICTURE ABOVE IS SAMPLE VALUES, and it has to say so. The canvas cannot resolve a dataset,
	// so the chart is drawn over values derived from the field names — a plausible picture of the
	// right shape, and not this client's figures. Unlabelled, an author could take a number off it.
	const sampleNote = `<span style="font-size:9px;color:#94a3b8">· sample values</span>`;
	const detail = hasData
		? `<div style="display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:3px">` +
			`${bits.join('')}${sampleNote}</div>`
		: `<div style="display:flex;align-items:center;gap:4px;margin-top:3px;font-size:9.5px;` +
			`color:#b45309"><span style="font-size:11px;line-height:1">!</span>` +
			`<span>No data source — double-click to configure</span></div>`;

	return (
		// AN OVERLAY, not a band beneath the chart — and that is about SIZE, not tidiness. A band took
		// its height out of the chart's area while `toHTML` exports the whole element as
		// `data-height`, so the document drew the chart taller than the canvas ever showed it. Over
		// the top, the chart's area IS the exported box and the two agree.
		//
		// Top RIGHT: a chart's own title sits top-centre and its y axis runs down the left, so the
		// right is the one corner that is reliably free. Capped at 46% so it can never cover the plot.
		//
		// IT HAS TO LIFT OFF THE DRAWING. At a near-transparent wash with a hairline shadow this sat
		// flat against the plot and read as part of the figure — a legend, or an annotation the client
		// would see in the finished document. It is neither: it is the editor talking to the author.
		// So it is nearly opaque, carries its own border, and casts a real drop shadow, which together
		// say "panel floating above" rather than "ink on the chart". Still a wash rather than flat
		// white, so a background the author set on the frame tints through it instead of being denied.
		//
		// `pointer-events: none` because the gesture that opens the config is a double-click ON THE
		// CHART, and a panel that swallowed it would make the component unconfigurable exactly where
		// it explains itself.
		`<div style="position:absolute;top:6px;right:6px;z-index:2;max-width:46%;` +
		`border-radius:7px;background:rgba(255,255,255,0.97);border:1px solid rgba(15,23,42,0.1);` +
		`box-shadow:0 6px 16px -4px rgba(15,23,42,0.22),0 2px 5px -1px rgba(15,23,42,0.14);` +
		`padding:6px 9px 7px;pointer-events:none;` +
		`font-family:Arial,sans-serif">${heading}${eyebrow}${titleLine}${detail}</div>`
	);
}

/** The exported chart-frame wrapper. Pure, and exported, because the rule it encodes — that the id
 *  and the classes must survive — is the one thing about this element that has silently broken
 *  before, and a `toHTML` on a live GrapesJS model is not something a unit test can reach.
 *
 *  `id` is emitted whenever the component has one: GrapesJS keys a style rule `#<id>`, and there is
 *  no cheap way here to know whether a rule exists, so the attribute is unconditional rather than
 *  guessed at. Order is id, class, style — style last so an inline declaration from an older
 *  stored template still wins over a rule, as it does in the browser.
 */
export interface WrapperOpts {
	id?: string;
	classes?: readonly string[];
	style?: Record<string, string>;
	/** Extra `data-*` a type needs for its own round trip, emitted verbatim. */
	data?: Record<string, string>;
	inner?: string;
}

/** Serialise a component wrapper that owns a custom `toHTML`.
 *
 *  EVERY such type has to go through this, and the reason is a defect all three of them had: a
 *  hand-written `toHTML` that emits only `data-gjs-type` silently discards the component's own id,
 *  classes and style. GrapesJS 0.23 defaults `avoidInlineStyle: true`, so a style set from the rail
 *  is a CSS rule keyed `#<id>` — dropping the id drops the rule — and any inline style an older
 *  document carries is dropped outright. For the two TABLES that meant a width an author set was
 *  thrown away on export and the inner table's `width:100%` filled the page, so a table could never
 *  be made narrower than its container no matter what was done to it.
 */
export function wrapperHtml(gjsType: string, opts: WrapperOpts = {}): string {
	// `escHtml` is for TEXT and leaves `"` alone; these are attribute values, and a style value
	// legitimately carries one — `font-family:"Helvetica Neue"` would end the attribute early.
	const escAttr = (v: string): string => escHtml(v).replace(/"/g, '&quot;');
	const attrs: string[] = [`data-gjs-type="${escAttr(gjsType)}"`];
	if (opts.id) attrs.push(`id="${escAttr(opts.id)}"`);
	const classes = (opts.classes ?? []).filter(Boolean);
	if (classes.length) attrs.push(`class="${escAttr(classes.join(' '))}"`);
	for (const [k, v] of Object.entries(opts.data ?? {})) attrs.push(`${k}="${escAttr(v)}"`);
	const style = Object.entries(opts.style ?? {})
		.filter(([, v]) => v != null && String(v) !== '')
		.map(([k, v]) => `${k}:${v}`)
		.join(';');
	if (style) attrs.push(`style="${escAttr(style)}"`);
	return `<div ${attrs.join(' ')}>${opts.inner ?? ''}</div>`;
}

export function chartFrameHtml(opts: {
	id?: string;
	classes?: readonly string[];
	style?: Record<string, string>;
	inner?: string;
}): string {
	return wrapperHtml('chart-frame', opts);
}

/** THE CANVAS PREVIEW: the REAL chart, drawn by the real renderer, over SAMPLE rows.
 *
 * Not a picture of a chart and not an abstract mark. `renderChartSvg` is the same code the drawer's
 * preview uses and a line-for-line mirror of the worker's `charts.py`, so what an author sees on the
 * page is the chart they will get — the type, the bound fields, the colours, the grid, the legend —
 * with sample numbers standing in for the client's. The canvas cannot resolve a dataset (that is a
 * request per component, per keystroke), so sample values are the only honest content available to
 * it, and `chartConfigCaption` beneath says so.
 *
 * What this replaced, twice, and why neither worked. First a hand-drawn chart over INVENTED
 * specifics — 'Global Equity 45% £45,000', bars labelled Q1-Q5 — which an author read as figures and
 * which reflected neither their chart type nor their binding. Then an abstract glyph, honest but
 * mute: it said "a chart goes here" and answered nothing about what the chart would look like.
 * Using the real renderer is what makes the canvas answer that.
 *
 * `sampleTableRows` keys its values off the FIELD NAMES, so an amount reads as money and a fund as a
 * fund name, and `toNumber` parses those money strings back out for the axes. A comp with no fields
 * bound gets the renderer's own empty state — a dashed frame naming what is missing — which is
 * better than a mark that looks complete.
 *
 * `hBg`/`hFg` are the table's header colours; a chart's colours are its own props.
 */
export function chartPreviewHtml(
	type: string,
	hBg: string,
	hFg: string,
	attrs: Record<string, string> = {},
	palette?: readonly string[],
	width = 600,
	height = 340
): string {
	const wrap = (inner: string): string =>
		`<div style="width:100%;height:100%;display:flex;align-items:stretch;` +
		`justify-content:center;overflow:hidden">${inner}</div>`;

	if (type === 'data_table') {
		const cols = (attrs['data-comp-columns'] ?? '')
			.split(',')
			.map((c) => c.trim())
			.filter(Boolean);
		if (!cols.length) return wrap(EMPTY_TABLE_NOTE);
		const headers = (attrs['data-comp-headers'] ?? '').split(',').map((h) => h.trim());
		const design: JDesign = {
			...defaultJDesign(),
			headerBg: hBg,
			headerFg: hFg,
			striped: (attrs['data-comp-striped'] ?? 'true') !== 'false'
		};
		const widths = (attrs['data-comp-widths'] ?? '').split(',').map((w) => w.trim());
		const aligns = (attrs['data-comp-aligns'] ?? '').split(',').map((a) => a.trim());
		return wrap(
			`<div style="width:100%;align-self:flex-start">` +
				tablePreviewInner(
					design,
					cols.map((c, i) => headers[i] || c),
					sampleTableRows(cols, 4),
					cols.map((_, i) => ({
						width: widths[i] && Number(widths[i]) > 0 ? `${Number(widths[i])}%` : '',
						align: aligns[i] || 'left'
					}))
				) +
				`</div>`
		);
	}

	const comp = previewCompFromAttrs({ ...attrs, 'data-comp-type': type }, palette);
	const fields = boundFields(comp);
	const rows = fields.length
		? sampleTableRows(fields, 5).map((r) => Object.fromEntries(fields.map((f, i) => [f, r[i]])))
		: [];
	// THE CALLER'S BOX, never a fixed one. `toHTML` exports this component's own measured size as
	// `data-width`/`data-height` and the worker lays the chart out at those numbers, so drawing here
	// at 600×340 and letting the SVG fit itself into the card meant the canvas showed a DIFFERENT
	// chart: fitted and centred with empty bands either side, and its axis divided over a height the
	// document would never use. Same numbers on both sides, same picture.
	return wrap(renderChartSvg(comp, rows, width, height));
}

// Standard regulated boilerplate for the doc-risk-warning smart component, keyed by its `variant`
// trait. Firm-agnostic and lightly worded — an author can edit the copy after dropping it (the text is
// RTE-editable), and switching the variant swaps in the standard wording. These are sensible defaults,
// not legal advice: compliance should sign off the exact wording per firm.
const WARN_COPY: Record<string, string> = {
	investment:
		'The value of investments and the income from them can fall as well as rise. You may get back ' +
		'less than you invested. Past performance is not a reliable indicator of future results.',
	pension:
		'This is a pension designed to provide benefits in retirement. You cannot normally access your ' +
		'pension savings until age 55 (57 from April 2028). The value of your pension is not guaranteed ' +
		'and can fall as well as rise. Tax treatment depends on your individual circumstances and may change.',
	capital:
		'Capital at risk. The value of your investment can go down as well as up and you may get back ' +
		'less than you invested.',
	past:
		'Past performance is not a reliable indicator of future results and should not be relied upon ' +
		'when making investment decisions.'
};
const WARN_STYLE: Record<string, string> = {
	background: '#fff7ed',
	border: '1px solid #fed7aa',
	'border-radius': '6px',
	padding: '10px 12px',
	margin: '10px 0',
	'font-size': '11px',
	color: '#9a3412',
	'line-height': '1.5'
};

// getSchema is reserved for schema-aware component rendering (see file header); kept in the
// signature so callers pass it positionally.
export function registerComponentTypes(
	editor: any,
	getSchema: () => VarSchema,
	getDatasetSample?: (code: string) => { columns: string[]; rows: unknown[][] } | null
): void {
	const dc = editor.DomComponents;

	// The Component tab never surfaces the built-in id/title traits. Clearing them on the
	// base `default` type drops them everywhere — containers (doc-section) and any other
	// type that doesn't declare its own traits now show an empty trait list instead of the
	// stray "Id"/"Title" fields. Types that want traits (doc-divider, chart) still set them.
	dc.addType('default', { model: { defaults: { traits: [] } } });

	// GrapesJS ships built-in table component types (table/thead/tbody/tfoot/row/cell) that carry
	// the default id/title traits AND are individually selectable — clutter for our tables, where
	// only a cell (type=text) or the wrapper (doc-jtable / doc-texttable) is worth selecting.
	// Clear their traits, and make the structural wrappers non-selectable so a click passes through
	// to the cell or the table wrapper rather than landing on a bare <tr>/<table>.
	/** ONE LAYER PER TABLE. A table is a tree — table > thead > tr > th×n > tbody > tr > td×n — and
	 *  every node of it was its own row in the Layers panel, so a single three-column table buried
	 *  the document's real structure under thirty entries. A chart is one row because its interior is
	 *  declared `layerable: false`; a table's interior is assembled from GrapesJS's own table types
	 *  AND from RTE-editable cells that the re-parse retypes as `text`, so declaring it per type
	 *  cannot cover it. Marking from the WRAPPER covers whatever the table actually contains.
	 *
	 *  `layerable`, never `selectable`: a cell must stay selectable and editable, because it is where
	 *  the author types. What it must not be is a line in the outline.
	 *
	 *  Re-run whenever the interior changes — a design edit or a template change rebuilds the cells,
	 *  and a fresh cell arrives layerable.
	 */
	function hideInteriorFromLayers(comp: any): void {
		comp?.components?.()?.forEach?.((child: any) => {
			if (child.get?.('layerable') !== false) child.set?.('layerable', false);
			hideInteriorFromLayers(child);
		});
	}

	function keepInteriorOutOfLayers(comp: any): void {
		hideInteriorFromLayers(comp);
		const kids = comp?.components?.();
		comp?.listenTo?.(kids, 'add reset change:components', () => hideInteriorFromLayers(comp));
	}

	for (const t of ['table', 'thead', 'tbody', 'tfoot', 'row']) {
		dc.addType(t, {
			model: { defaults: { traits: [], selectable: false, hoverable: false, layerable: false } }
		});
	}
	// A cell keeps its traits cleared and stays SELECTABLE — it is where the author types — but it is
	// not an outline row. See `hideInteriorFromLayers`.
	dc.addType('cell', { model: { defaults: { traits: [], layerable: false } } });

	// ── doc-divider — a horizontal rule; fully trait-driven (colour/thickness/style/width/spacing) ──
	dc.addType('doc-divider', {
		model: {
			defaults: {
				name: 'Divider',
				icon: LAYER_ICONS.divider,
				tagName: 'div',
				droppable: false,
				editable: false,
				selectable: true,
				hoverable: true,
				dividerColor: '#000000',
				dividerThickness: 1,
				dividerStyle: 'solid',
				dividerWidth: 100,
				dividerSpacing: 16,
				// Fully trait-driven: no Style sectors (noStyle hides the whole Style panel).
				noStyle: true,
				unstylable: showOnlySectors(),
				style: {
					display: 'inline-block',
					width: '100%',
					height: '1px',
					background: '#000000',
					margin: '16px 0',
					'box-sizing': 'border-box'
				},
				traits: [
					{ type: 'color', label: 'Colour', name: 'dividerColor', changeProp: true },
					{
						type: 'number',
						label: 'Thickness (px)',
						name: 'dividerThickness',
						changeProp: true,
						min: 1
					},
					{
						type: 'select',
						label: 'Line style',
						name: 'dividerStyle',
						changeProp: true,
						options: [
							{ id: 'solid', name: 'Solid' },
							{ id: 'dashed', name: 'Dashed' },
							{ id: 'dotted', name: 'Dotted' }
						]
					},
					{
						type: 'number',
						label: 'Width (%)',
						name: 'dividerWidth',
						changeProp: true,
						min: 1,
						max: 100
					},
					{
						type: 'number',
						label: 'Spacing (px)',
						name: 'dividerSpacing',
						changeProp: true,
						min: 0
					}
				]
			},
			init(this: any) {
				this.on(
					'change:dividerColor change:dividerThickness change:dividerStyle change:dividerWidth change:dividerSpacing',
					this.updateDivider
				);
			},
			updateDivider(this: any) {
				const color = (this.get('dividerColor') as string) || '#000000';
				const thickness = Number(this.get('dividerThickness')) || 1;
				const lineStyle = (this.get('dividerStyle') as string) || 'solid';
				const width = Number(this.get('dividerWidth')) || 100;
				const spacingRaw = this.get('dividerSpacing');
				const spacing = spacingRaw == null ? 16 : Number(spacingRaw);
				if (lineStyle === 'solid') {
					// Solid: a filled bar (crisp at 1px); no border.
					this.addStyle({
						background: color,
						height: `${thickness}px`,
						width: `${width}%`,
						'border-top': '0',
						margin: `${spacing}px 0`
					});
				} else {
					// Dashed/dotted can't be expressed by a background — use a top border, zero height.
					this.addStyle({
						background: 'transparent',
						height: '0',
						width: `${width}%`,
						'border-top': `${thickness}px ${lineStyle} ${color}`,
						margin: `${spacing}px 0`
					});
				}
			}
		},
		view: {
			onRender(this: any) {
				this.model.updateDivider();
			}
		}
	});

	// ── doc-page — an A4 page wrapper. A selectable component; its page-layout options
	//    (size/orientation/margins) are exposed as component traits below. ──
	dc.addType('doc-page', {
		isComponent: (el: Element) => (el as HTMLElement).classList?.contains('page'),
		model: {
			defaults: {
				name: 'Page',
				icon: LAYER_ICONS.page,
				tagName: 'div',
				classes: ['page'],
				draggable: false,
				copyable: false,
				removable: false,
				droppable: true,
				selectable: true,
				hoverable: true,
				resizable: false,
				highlightable: true,
				// The page is a fixed top-level container — no move/clone/delete, and the
				// "select parent" (↑) action is meaningless here, so give it no toolbar.
				toolbar: [],
				// Page-layout options, surfaced as component traits (mirrors defaultLayout()).
				// changeProp:true binds each to a model prop (not a DOM attribute); the Svelte
				// layer listens for component:update:<prop> and drives applyPageLayout globally.
				pageSize: 'A4',
				pageOrientation: 'portrait',
				marginTop: 19,
				marginRight: 19,
				marginBottom: 19,
				marginLeft: 19,
				// applyPageLayout owns width/min-height/padding and size/margins are trait-owned,
				// so hide Layout, Size & Space and Position — leaving Background, Borders,
				// Typography and Effects for the page.
				unstylable: showOnlySectors('background', 'borders', 'typography', 'effects'),
				traits: [
					{
						type: 'select',
						label: 'Paper size',
						name: 'pageSize',
						changeProp: true,
						options: [
							{ id: 'A4', name: 'A4' },
							{ id: 'A5', name: 'A5' },
							{ id: 'A3', name: 'A3' },
							{ id: 'Letter', name: 'Letter' },
							{ id: 'Legal', name: 'Legal' }
						]
					},
					{
						type: 'select',
						label: 'Orientation',
						name: 'pageOrientation',
						changeProp: true,
						options: [
							{ id: 'portrait', name: 'Portrait' },
							{ id: 'landscape', name: 'Landscape' }
						]
					},
					{ type: 'number', label: 'Margin top (mm)', name: 'marginTop', changeProp: true, min: 0 },
					{
						type: 'number',
						label: 'Margin right (mm)',
						name: 'marginRight',
						changeProp: true,
						min: 0
					},
					{
						type: 'number',
						label: 'Margin bottom (mm)',
						name: 'marginBottom',
						changeProp: true,
						min: 0
					},
					{
						type: 'number',
						label: 'Margin left (mm)',
						name: 'marginLeft',
						changeProp: true,
						min: 0
					}
				]
			},
			// Single source of truth for page names: a user rename (custom-name, set via the
			// layer tree's dblclick edit) wins; otherwise the 1-based position among sibling
			// pages. The layer tree and the Pages rail both read this.
			getName(this: any): string {
				const custom = this.get('custom-name');
				if (custom) return custom;
				const siblings =
					this.parent?.()
						?.components?.()
						.filter((c: any) => c.getClasses?.().includes('page')) ?? [];
				const idx = siblings.indexOf(this);
				return idx >= 0 ? `Page ${idx + 1}` : 'Page';
			}
		}
	});

	// ── layout cells — invisible to selection; clicks go to their content ──
	dc.addType('doc-col', {
		isComponent: (el: Element) => (el as HTMLElement).classList?.contains('doc-col'),
		model: {
			defaults: {
				icon: LAYER_ICONS.col,
				draggable: false,
				droppable: true,
				selectable: false,
				hoverable: false,
				copyable: false,
				removable: false
			},
			getName() {
				return 'Column';
			}
		}
	});

	dc.addType('doc-section', {
		isComponent: (el: Element) => (el as HTMLElement).classList?.contains('doc-section'),
		model: {
			defaults: {
				icon: LAYER_ICONS.section,
				draggable: true,
				droppable: false,
				selectable: true,
				hoverable: true,
				copyable: true,
				removable: true,
				// Container: keep Layout/Size/Background/Borders/Effects; hide Position + Typography.
				unstylable: hideSectors('position', 'typography')
			},
			getName() {
				return 'Section';
			}
		}
	});

	// ── text — treat heading/paragraph/list tags as editable text. No default
	//    id/title traits (the prototype's Component tab doesn't surface them). ──
	dc.addType('text', {
		isComponent: (el: Element) => {
			if ((el as HTMLElement).getAttribute?.('data-gjs-type') === 'text') return { type: 'text' };
			if (TEXT_TAGS.has(el.tagName?.toUpperCase())) return { type: 'text' };
			return false;
		},
		model: {
			// Hide Layout + Position. Layout in particular: display:flex blockifies a text box's
			// children, so an inline RTE run (<span> foreColor) would jump out as its own box —
			// keeping Layout off preserves inline formatting. Text keeps Typography/Size/Background/
			// Borders/Effects.
			defaults: {
				icon: LAYER_ICONS.text,
				traits: [],
				unstylable: showOnlySectors('typography', 'size', 'background', 'borders', 'effects'),
				selectable: true,
				hoverable: true
			},
			// Semantic target-bar label by tag: "Heading 2" / "Paragraph" / "List" / "Quote".
			getName(this: any): string {
				const tag = String(this.get('tagName') ?? '').toUpperCase();
				if (/^H[1-6]$/.test(tag)) return `Heading ${tag[1]}`;
				if (tag === 'P') return 'Paragraph';
				if (tag === 'UL' || tag === 'OL') return 'List';
				if (tag === 'BLOCKQUOTE') return 'Quote';
				return 'Text';
			}
		}
	});

	// ── image — customise the grapesjs-blocks-basic <img> type: a Graphene name +
	//    curated traits (Alt, object-fit, rounded). src editing stays the built-in
	//    double-click asset manager. addType on an existing id extends it. ──
	dc.addType('image', {
		model: {
			defaults: {
				name: 'Image',
				icon: LAYER_ICONS.image,
				objectFit: '',
				rounded: false,
				// Keep Layout (the Text-wrap/float control), Size, Borders, Position, Effects;
				// hide Background + Typography.
				// Layout is narrowed to what an IMAGE can answer. The sector's other five properties —
				// flex-direction, justify-content, align-items, flex-wrap, gap — arrange a container's
				// CHILDREN, and an <img> is a replaced element with none. They were five controls that
				// could never do anything. `display` and `float` (wrap text around the image) are real.
				unstylable: [
					...hideSectors('background', 'typography'),
					...sectorPropsExcept('layout', 'display', 'float')
				],
				traits: [
					{ type: 'text', label: 'Alt text', name: 'alt' },
					{
						type: 'select',
						label: 'Fit',
						name: 'objectFit',
						changeProp: true,
						options: [
							{ id: '', name: 'Default' },
							{ id: 'cover', name: 'Cover' },
							{ id: 'contain', name: 'Contain' },
							{ id: 'fill', name: 'Fill' },
							{ id: 'none', name: 'None' }
						]
					},
					{ type: 'checkbox', label: 'Rounded', name: 'rounded', changeProp: true }
				]
			},
			init(this: any) {
				this.on('change:objectFit', this.updateFit);
				this.on('change:rounded', this.updateRounded);
			},
			updateFit(this: any) {
				this.addStyle({ 'object-fit': (this.get('objectFit') as string) || '' });
			},
			updateRounded(this: any) {
				this.addStyle({ 'border-radius': this.get('rounded') ? '8px' : '' });
			}
		}
	});

	// ── doc-callout — a note/highlight box. Extends `text` so the body is RTE-editable
	//    (and holds variables); a `variant` trait switches note (left accent) vs highlight
	//    (full tint), and `accent` recolours it. Replaces the old bare-<div> note/highlight
	//    blocks that lost their identity on drop. ──
	dc.addType('doc-callout', {
		extend: 'text',
		isComponent: (el: Element) =>
			(el as HTMLElement).getAttribute?.('data-gjs-type') === 'doc-callout'
				? { type: 'doc-callout' }
				: false,
		model: {
			defaults: {
				name: 'Callout',
				icon: LAYER_ICONS.callout,
				calloutVariant: 'note',
				calloutAccent: '#3b82f6',
				// Holds RTE text, so (like text) hide Layout too; keep Background, Borders,
				// Typography, Size, Effects. Hide Position.
				unstylable: hideSectors('position', 'layout'),
				traits: [
					{
						type: 'select',
						label: 'Variant',
						name: 'calloutVariant',
						changeProp: true,
						options: [
							{ id: 'note', name: 'Note' },
							{ id: 'highlight', name: 'Highlight' }
						]
					},
					{ type: 'color', label: 'Accent', name: 'calloutAccent', changeProp: true }
				]
			},
			getName(this: any): string {
				return this.get('calloutVariant') === 'highlight' ? 'Highlight' : 'Note';
			},
			init(this: any) {
				// Seed props from the block's data-* attributes (blocks are HTML strings).
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				if (attrs['data-variant']) this.set('calloutVariant', attrs['data-variant']);
				if (attrs['data-accent']) this.set('calloutAccent', attrs['data-accent']);
				this.on('change:calloutVariant change:calloutAccent', this.updateCallout);
			},
			updateCallout(this: any) {
				const accent = (this.get('calloutAccent') as string) || '#3b82f6';
				const variant = (this.get('calloutVariant') as string) || 'note';
				if (variant === 'highlight') {
					this.addStyle({
						background: `${accent}14`,
						border: `1px solid ${accent}55`,
						'border-radius': '6px',
						padding: '12px 14px',
						margin: '8px 0'
					});
				} else {
					this.addStyle({
						background: `${accent}10`,
						border: '0',
						'border-left': `3px solid ${accent}`,
						'border-radius': '0 4px 4px 0',
						padding: '12px 14px',
						margin: '8px 0'
					});
				}
			}
		},
		view: {
			onRender(this: any) {
				this.model.updateCallout();
			}
		}
	});

	// ── doc-risk-warning — a regulated risk/disclaimer box. A `variant` trait swaps the standard
	//    boilerplate (WARN_COPY) + styles it as an amber warning band. The text is RTE-editable, so an
	//    author can adjust the wording; changing the variant re-seeds the standard copy. onRender only
	//    re-applies the STYLE (never the text), so an author's edits survive a reload. ──
	dc.addType('doc-risk-warning', {
		isComponent: (el: Element) =>
			(el as HTMLElement).getAttribute?.('data-gjs-type') === 'doc-risk-warning'
				? { type: 'doc-risk-warning' }
				: false,
		model: {
			defaults: {
				name: 'Risk warning',
				icon: LAYER_ICONS.warning,
				warnVariant: 'investment',
				// Holds RTE text, so (like text/callout) hide Layout; keep Background/Borders/Typography.
				unstylable: hideSectors('position', 'layout'),
				traits: [
					{
						type: 'select',
						label: 'Warning',
						name: 'warnVariant',
						changeProp: true,
						options: [
							{ id: 'investment', name: 'Investment — capital at risk' },
							{ id: 'pension', name: 'Pension — retirement' },
							{ id: 'capital', name: 'Capital at risk (short)' },
							{ id: 'past', name: 'Past performance' }
						]
					}
				]
			},
			getName(): string {
				return 'Risk warning';
			},
			init(this: any) {
				// Seed the variant from the block's data-variant, then swap the copy whenever it changes.
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				if (attrs['data-variant']) this.set('warnVariant', attrs['data-variant']);
				this.on('change:warnVariant', () => {
					const v = (this.get('warnVariant') as string) || 'investment';
					this.components(WARN_COPY[v] ?? WARN_COPY.investment);
					this.addStyle({ ...WARN_STYLE });
				});
			},
			styleWarn(this: any) {
				this.addStyle({ ...WARN_STYLE });
			}
		},
		view: {
			onRender(this: any) {
				// Style only — never re-seed the text, so author edits survive load/render.
				this.model.styleWarn();
			}
		}
	});

	// ── doc-field — a "Label + Value" row. A container holding two editable text cells;
	//    a Layout trait switches inline (row) vs stacked (column). The value cell holds
	//    RTE text/variables. Replaces the old bare-<div> label+value block. ──
	dc.addType('doc-field', {
		isComponent: (el: Element) =>
			(el as HTMLElement).getAttribute?.('data-gjs-type') === 'doc-field'
				? { type: 'doc-field' }
				: false,
		model: {
			defaults: {
				name: 'Field',
				icon: LAYER_ICONS.field,
				selectable: true,
				hoverable: true,
				droppable: false,
				draggable: true,
				copyable: true,
				removable: true,
				fieldLayout: 'inline',
				// Layout is trait-driven; keep Typography/Size/Background/Borders/Effects.
				unstylable: hideSectors('position', 'layout'),
				traits: [
					{
						type: 'select',
						label: 'Layout',
						name: 'fieldLayout',
						changeProp: true,
						options: [
							{ id: 'inline', name: 'Inline' },
							{ id: 'stacked', name: 'Stacked' }
						]
					}
				]
			},
			getName() {
				return 'Field';
			},
			init(this: any) {
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				if (attrs['data-layout']) this.set('fieldLayout', attrs['data-layout']);
				this.on('change:fieldLayout', this.updateField);
			},
			updateField(this: any) {
				const layout = (this.get('fieldLayout') as string) || 'inline';
				if (layout === 'stacked') {
					this.addStyle({
						display: 'flex',
						'flex-direction': 'column',
						'align-items': 'flex-start',
						gap: '2px'
					});
				} else {
					this.addStyle({
						display: 'flex',
						'flex-direction': 'row',
						'align-items': 'baseline',
						gap: '8px'
					});
				}
			}
		},
		view: {
			onRender(this: any) {
				this.model.updateField();
			}
		}
	});

	// ── doc-jtable — a jinja-driven table. Canvas shows the loop body once (with the
	//    {{ row.field }} tokens visible); the structured form (dataset + columns) and the
	//    raw jinja/HTML template are edited from the right panel. toHTML emits the full
	//    template (with {% for %}) for the render pipeline. Child cells are locked (the
	//    table is edited via the panel, not by clicking individual cells).
	dc.addType('doc-jtable', {
		isComponent: (el: Element) =>
			(el as HTMLElement).getAttribute?.('data-gjs-type') === 'doc-jtable'
				? { type: 'doc-jtable' }
				: false,
		model: {
			defaults: {
				name: 'Table',
				icon: LAYER_ICONS.table,
				selectable: true,
				hoverable: true,
				// DRAGGABLE NARROWER, and on the horizontal axis ONLY. A table's height is its rows —
				// there is nothing a height handle could set that the content does not already decide —
				// and a handle that changes nothing is the failure this editor keeps producing. The two
				// side handles are the whole of it, and the width they write now survives export
				// (`wrapperHtml`), which it did not before.
				resizable: { tl: 0, tc: 0, tr: 0, cl: 1, cr: 1, bl: 0, bc: 0, br: 0 },
				droppable: false,
				draggable: true,
				copyable: true,
				removable: true,
				editable: false,
				// Table paints its own grid; keep Typography/Size/Background/Borders/Effects.
				unstylable: hideSectors('layout', 'position'),
				traits: [],
				jtemplate: '',
				jdataset: 'items',
				jcolumns: [],
				jdesign: defaultJDesign()
			},
			getName() {
				return 'Table';
			},
			init(this: any) {
				keepInteriorOutOfLayers(this);
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				let tmpl = '';
				if (attrs['data-template']) {
					try {
						tmpl = decodeURIComponent(attrs['data-template']);
					} catch {
						tmpl = '';
					}
				}
				if (!tmpl) {
					const ds = attrs['data-dataset'] || 'items';
					let cols = defaultJColumns();
					if (attrs['data-columns']) {
						try {
							cols = JSON.parse(attrs['data-columns']);
						} catch {
							/* keep default columns */
						}
					}
					tmpl = buildJTemplate(ds, cols);
				}
				let design = defaultJDesign();
				if (attrs['data-jdesign']) {
					try {
						design = normaliseJDesign(JSON.parse(decodeURIComponent(attrs['data-jdesign'])));
					} catch {
						/* keep default design */
					}
				}
				const parsed = parseJTemplate(tmpl);
				this.set('jtemplate', tmpl, { silent: true });
				this.set('jdataset', parsed?.dataset ?? (attrs['data-dataset'] || 'items'), {
					silent: true
				});
				this.set('jcolumns', parsed?.columns ?? defaultJColumns(), { silent: true });
				this.set('jdesign', design, { silent: true });
				this.renderJTable();
				this.on('change:jtemplate change:jdesign', this.renderJTable);
			},
			renderJTable(this: any) {
				const tmpl = (this.get('jtemplate') as string) || '';
				const design = normaliseJDesign(this.get('jdesign'));
				const parsed = parseJTemplate(tmpl);
				// Fill the canvas with the bound dataset's real sample rows so connecting a source
				// actually changes what's shown; fall back to the single {{ row.field }} token row when
				// the dataset has no sample (e.g. a live SQL dataset with no authored preview).
				const cols: JColumn[] = parsed?.columns ?? (this.get('jcolumns') as JColumn[]) ?? [];
				const ds = parsed?.dataset ?? (this.get('jdataset') as string) ?? '';
				const sample = getDatasetSample?.(ds) ?? null;
				const dataRows =
					sample && Array.isArray(sample.rows) && sample.rows.length && cols.length
						? jRowsFromSample(sample, cols)
						: null;
				this.components(
					dataRows
						? tablePreviewInner(
								design,
								cols.map((c) => c.header),
								dataRows
							)
						: applyJDesign(jtemplateToEditorHtml(tmpl), design)
				);
				const lock = (c: any) => {
					c.set({
						selectable: false,
						hoverable: false,
						draggable: false,
						removable: false,
						copyable: false,
						highlightable: false
					});
					c.get('components')?.each?.(lock);
				};
				this.get('components')?.each?.(lock);
				// Keep attributes in sync for project round-trip + serialisation.
				this.addAttributes({
					'data-template': encodeURIComponent(tmpl),
					'data-dataset': parsed?.dataset ?? (this.get('jdataset') || ''),
					'data-columns': JSON.stringify(parsed?.columns ?? this.get('jcolumns') ?? []),
					'data-jdesign': encodeURIComponent(JSON.stringify(design))
				});
			},
			toHTML(this: any): string {
				const tmpl = (this.get('jtemplate') as string) || '';
				const design = normaliseJDesign(this.get('jdesign'));
				// Design is baked into the table tag so the exported document renders styled; the
				// wrapper keeps a data-jdesign copy so reopening the template restores the panel.
				//
				// THROUGH `wrapperHtml`, which is what carries the wrapper's OWN id, classes and style.
				// Emitting a bare div threw them away, and with them any width the author had set — so
				// the inner table's `width:100%` filled the page and a table could not be made narrower
				// than its container by any means. See `wrapperHtml`.
				return wrapperHtml('doc-jtable', {
					id: this.getId?.(),
					classes: (this.getClasses?.() ?? []) as string[],
					style: (this.get('style') as Record<string, string>) ?? {},
					data: { 'data-jdesign': encodeURIComponent(JSON.stringify(design)) },
					inner: applyJDesign(tmpl, design)
				});
			}
		}
	});

	// ── doc-texttable — a static, typed table. Cells are real data-gjs-type=text children,
	//    so they're RTE-editable on the canvas and serialise as a plain <table>; the render
	//    pipeline needs no special handling. Rows/columns are added/removed from the right
	//    panel (see TemplateEditor). Shares the doc-jtable styling via `jdesign`. ──
	dc.addType('doc-texttable', {
		isComponent: (el: Element) =>
			(el as HTMLElement).getAttribute?.('data-gjs-type') === 'doc-texttable'
				? { type: 'doc-texttable' }
				: false,
		model: {
			defaults: {
				name: 'Text table',
				icon: LAYER_ICONS.table,
				selectable: true,
				hoverable: true,
				// DRAGGABLE NARROWER, and on the horizontal axis ONLY. A table's height is its rows —
				// there is nothing a height handle could set that the content does not already decide —
				// and a handle that changes nothing is the failure this editor keeps producing. The two
				// side handles are the whole of it, and the width they write now survives export
				// (`wrapperHtml`), which it did not before.
				resizable: { tl: 0, tc: 0, tr: 0, cl: 1, cr: 1, bl: 0, bc: 0, br: 0 },
				droppable: false,
				draggable: true,
				copyable: true,
				removable: true,
				// Table paints its own grid; keep Typography/Size/Background/Borders/Effects.
				unstylable: hideSectors('layout', 'position'),
				traits: [],
				jdesign: defaultJDesign()
			},
			getName() {
				return 'Text table';
			},
			init(this: any) {
				keepInteriorOutOfLayers(this);
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				let design = defaultJDesign();
				if (attrs['data-jdesign']) {
					try {
						design = normaliseJDesign(JSON.parse(decodeURIComponent(attrs['data-jdesign'])));
					} catch {
						/* keep default design */
					}
				}
				this.set('jdesign', design, { silent: true });
				// Fresh drop (no <table> child yet) → build a default grid; reopened → restyle in place.
				if (!this.get('components')?.length) {
					const rows = parseInt(attrs['data-rows'] || '3', 10);
					const cols = parseInt(attrs['data-cols'] || '2', 10);
					this.components(buildTextTableHtml(rows, cols, design));
				}
				this.applyDesignToTable();
				this.on('change:jdesign', this.applyDesignToTable);
			},
			/** Find the <table> child component (the only structural child). */
			tableComp(this: any) {
				let found: any = null;
				this.get('components')?.each?.((c: any) => {
					if (!found && String(c.get('tagName') || '').toLowerCase() === 'table') found = c;
				});
				return found;
			},
			applyDesignToTable(this: any) {
				const design = normaliseJDesign(this.get('jdesign'));
				const { cls, style } = jdesignTableAttrs(design);
				this.tableComp()?.addAttributes({ class: cls, style });
				this.addAttributes({ 'data-jdesign': encodeURIComponent(JSON.stringify(design)) });
			},
			/** Column count from the header row (falls back to the first body row). */
			colCount(this: any): number {
				const t = this.tableComp();
				if (!t) return 0;
				return t.find('thead th').length || t.find('tbody tr:first-child td').length || 0;
			},
			addRow(this: any) {
				const t = this.tableComp();
				const tbody = t?.find('tbody')[0];
				if (!tbody) return;
				const n = Math.max(1, this.colCount());
				tbody.append({
					tagName: 'tr',
					components: Array.from({ length: n }, () => ttCellDef('td', 'Text'))
				});
			},
			removeRow(this: any) {
				const rows = this.tableComp()?.find('tbody tr') ?? [];
				if (rows.length > 1) rows[rows.length - 1].remove();
			},
			addColumn(this: any) {
				const t = this.tableComp();
				if (!t) return;
				t.find('thead tr')[0]?.append(ttCellDef('th', 'Heading'));
				t.find('tbody tr').forEach((tr: any) => tr.append(ttCellDef('td', 'Text')));
			},
			removeColumn(this: any) {
				const t = this.tableComp();
				if (!t || this.colCount() <= 1) return;
				const dropLast = (tr: any, sel: string) => {
					const cells = tr.find(sel);
					if (cells.length) cells[cells.length - 1].remove();
				};
				t.find('thead tr').forEach((tr: any) => dropLast(tr, 'th'));
				t.find('tbody tr').forEach((tr: any) => dropLast(tr, 'td'));
			},
			toHTML(this: any, opts?: any): string {
				const design = normaliseJDesign(this.get('jdesign'));
				let inner = '';
				this.get('components')?.each?.((c: any) => {
					inner += c.toHTML?.(opts) ?? '';
				});
				// Through `wrapperHtml` for the reason the jtable gives: a bare div discarded the
				// wrapper's own id, classes and style, and with them any width the author had set.
				return wrapperHtml('doc-texttable', {
					id: this.getId?.(),
					classes: (this.getClasses?.() ?? []) as string[],
					style: (this.get('style') as Record<string, string>) ?? {},
					data: { 'data-jdesign': encodeURIComponent(JSON.stringify(design)) },
					inner
				});
			}
		}
	});

	// ── inline-fmt — <span>/<font> produced by the RTE (colour, font family, font size,
	//    and styleWithCSS bold/italic). Without this they fall to the default component
	//    type, which is selectable/draggable, so a colour change turned the run into its
	//    own movable "text box". Marking them non-selectable inline formatting keeps them
	//    part of the parent text flow (edited via the parent's RTE), never a separate box.
	dc.addType('inline-fmt', {
		isComponent: (el: Element) => {
			const tag = (el as HTMLElement).tagName?.toUpperCase();
			if (tag !== 'SPAN' && tag !== 'FONT') return false;
			if ((el as HTMLElement).getAttribute?.('data-gjs-type')) return false;
			if ((el as HTMLElement).classList?.contains('jinja-var')) return false;
			return { type: 'inline-fmt' };
		},
		model: {
			defaults: {
				selectable: false,
				hoverable: false,
				highlightable: false,
				draggable: false,
				droppable: false,
				layerable: false,
				editable: false
			}
		}
	});

	// ── jinja-var — an inline {{ variable }} chip, picked from the schema ──
	// isComponent returns the type so GrapesJS uses OUR defaults (selectable: false,
	// hoverable: false) instead of falling back to the built-in default type which is
	// selectable — that caused clicking a chip to exit text-RTE mode.
	// textable marks the chip as a first-class inline citizen of text components:
	// without it GrapesJS treats it as a foreign draggable block inside the text,
	// which lets edits/moves eject it and split the surrounding text runs into
	// sibling components (paragraphs visually breaking apart around the chip).
	// It renders contenteditable=false, so the chip deletes as one atomic token.
	dc.addType('jinja-var', {
		isComponent: (el: Element) =>
			(el as HTMLElement).classList?.contains('jinja-var') ? { type: 'jinja-var' } : false,
		model: {
			// selectable/hoverable so clicking the chip opens the variable + filter editor in the
			// right panel; textable:1 keeps it an atomic inline citizen of the surrounding text
			// (so selecting/editing it doesn't split the text into sibling components).
			defaults: {
				icon: LAYER_ICONS.jinjaVar,
				droppable: false,
				editable: false,
				selectable: true,
				hoverable: true,
				layerable: false,
				draggable: false,
				textable: 1
			},
			toHTML(this: any): string {
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				const key = attrs['data-jinja-key'] ?? '';
				return key ? `{{ ${key} }}` : '';
			}
		},
		view: {
			onRender(this: any) {
				this.updateChip();
				// Re-render the chip text when the key (or its filters) changes from the panel.
				if (!this.__jinjaWired) {
					this.__jinjaWired = true;
					this.listenTo(this.model, 'change:attributes', this.updateChip);
				}
			},
			updateChip(this: any) {
				const el = this.el as HTMLElement;
				const key =
					((this.model.get('attributes') as Record<string, string>) ?? {})['data-jinja-key'] ?? '';
				el.textContent = key ? `{{ ${key} }}` : '[ variable ]';
			}
		}
	});

	// ── stored markers — read-only placeholders for already-saved <g-chart>/<g-table>.
	//    Explicitly selectable so they can be labelled/removed, but never edited/dragged
	//    (they render in the PDF; reconfigure lands once their config is persisted). ──
	dc.addType('g-chart-stored', {
		isComponent: (el: Element) => el.tagName?.toLowerCase() === 'g-chart',
		model: {
			defaults: {
				icon: LAYER_ICONS.chart,
				tagName: 'g-chart',
				droppable: false,
				editable: false,
				resizable: false,
				draggable: false,
				copyable: false,
				selectable: true,
				hoverable: true,
				// Opaque render marker — no style sectors.
				noStyle: true,
				unstylable: showOnlySectors()
			},
			getName() {
				return 'Chart — saved';
			}
		},
		view: {
			onRender(this: any) {
				const el = this.el as HTMLElement;
				const id = el.getAttribute('data-id') ?? '?';
				el.style.cssText =
					'display:block;border:2px dashed #3b82f6;background:#eff6ff;padding:8px 12px;font-size:11px;color:#1e40af;border-radius:4px;margin:4px 0;';
				el.innerHTML = `<span>📊 Chart: ${id} (rendered in PDF)</span>`;
			}
		}
	});

	dc.addType('g-table-stored', {
		isComponent: (el: Element) => el.tagName?.toLowerCase() === 'g-table',
		model: {
			defaults: {
				icon: LAYER_ICONS.table,
				tagName: 'g-table',
				droppable: false,
				editable: false,
				resizable: false,
				draggable: false,
				copyable: false,
				selectable: true,
				hoverable: true,
				// Opaque render marker — no style sectors.
				noStyle: true,
				unstylable: showOnlySectors()
			},
			getName() {
				return 'Table — saved';
			}
		},
		view: {
			onRender(this: any) {
				const el = this.el as HTMLElement;
				el.style.cssText =
					'display:block;border:2px dashed #10b981;background:#f0fdf4;padding:8px 12px;font-size:11px;color:#065f46;border-radius:4px;margin:4px 0;';
				el.innerHTML = `<span>📋 Table: ${el.getAttribute('data-id') ?? '?'} (rendered in PDF)</span>`;
			}
		}
	});

	// ── repeat — emits {% for row in key %}…{% endfor %}. The loop source (data-loop-key) and the
	//    loop variable (data-item-var, default `row`) are set from the Repeat panel in TemplateEditor.
	//    `row` matches the data table + the variable catalogue, so catalogue `row.<col>` tokens dropped
	//    inside the loop resolve. ──
	dc.addType('repeat', {
		isComponent: (el: HTMLElement) => el.tagName === 'DIV' && el.dataset?.gjsType === 'repeat',
		model: {
			defaults: {
				icon: LAYER_ICONS.repeat,
				droppable: true,
				editable: false,
				resizable: false,
				selectable: true,
				hoverable: true,
				// Structural — emits {% for %} with no visual box of its own.
				noStyle: true,
				unstylable: showOnlySectors()
			},
			getName() {
				return 'Repeat (loop)';
			},
			toHTML(this: any, opts?: any): string {
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				const key = attrs['data-loop-key'];
				if (!key) return '<!-- repeat: no collection selected -->';
				const itemVar = attrs['data-item-var'] || 'row';
				let inner = '';
				(this.components() as any).each((c: any) => {
					const cAttrs = (c.get?.('attributes') ?? {}) as Record<string, string>;
					if (!cAttrs['data-editor-label']) inner += c.toHTML?.(opts) ?? '';
				});
				return `{% for ${itemVar} in ${key} %}${inner}{% endfor %}`;
			}
		}
	});

	dc.addType('cond-branch', {
		isComponent: (el: HTMLElement) => el.tagName === 'DIV' && el.dataset?.gjsType === 'cond-branch',
		model: {
			defaults: {
				icon: LAYER_ICONS.condBranch,
				droppable: true,
				editable: false,
				resizable: false,
				selectable: false,
				hoverable: false
			},
			// Not selectable, but it shows in the layer tree — label it by branch.
			getName(this: any): string {
				const branch = ((this.get('attributes') as Record<string, string>) ?? {})['data-branch'];
				return branch === 'else' ? 'Else branch' : 'If branch';
			},
			toHTML(this: any, opts?: any): string {
				let inner = '';
				(this.components() as any).each((c: any) => {
					const cAttrs = (c.get?.('attributes') ?? {}) as Record<string, string>;
					if (!cAttrs['data-branch-label']) inner += c.toHTML?.(opts) ?? '';
				});
				return inner;
			}
		}
	});

	// ── conditional — emits {% if expr %}…{% else %}…{% endif %}. Placeholder: honest flags +
	//    label now; the condition trait UI lands with the logic engine. ──
	dc.addType('conditional', {
		isComponent: (el: HTMLElement) => el.tagName === 'DIV' && el.dataset?.gjsType === 'conditional',
		model: {
			defaults: {
				icon: LAYER_ICONS.conditional,
				droppable: false,
				editable: false,
				resizable: false,
				selectable: true,
				hoverable: true,
				// Structural — emits {% if %} with no visual box of its own.
				noStyle: true,
				unstylable: showOnlySectors()
			},
			getName() {
				return 'Conditional';
			},
			toHTML(this: any, opts?: any): string {
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				const cond = attrs['data-condition'];
				if (!cond) return '<!-- conditional: no condition set -->';
				const action = attrs['data-condition-action'] ?? 'show';
				const expr = action === 'hide' ? `not (${cond})` : cond;
				const hasElse = attrs['data-has-else'] === 'true';

				let ifContent = '';
				let elseContent = '';
				(this.components() as any).each((c: any) => {
					const cAttrs = (c.get?.('attributes') ?? {}) as Record<string, string>;
					if (cAttrs['data-editor-label']) return;
					if (c.get?.('type') === 'cond-branch') {
						const branch = cAttrs['data-branch'];
						const branchHtml = c.toHTML?.(opts) ?? '';
						if (branch === 'else') elseContent = branchHtml;
						else ifContent = branchHtml;
					} else {
						ifContent += c.toHTML?.(opts) ?? '';
					}
				});

				if (hasElse) return `{% if ${expr} %}${ifContent}{% else %}${elseContent}{% endif %}`;
				return `{% if ${expr} %}${ifContent}{% endif %}`;
			}
		}
	});

	// ── chart / data_table — emits a <g-chart>/<g-table> marker when a data source is set ──
	dc.addType('chart', {
		isComponent: (el: HTMLElement) => el.tagName === 'DIV' && !!el.dataset?.compId,
		model: {
			// The chart lives inside a `chart-frame` (its single stylable unit — see below). Selecting
			// the chart is redirected to the frame (see TemplateEditor component:selected), so the
			// group acts as one item; the chart is hidden from the layer tree and can't be dragged /
			// deleted on its own. Its view renders the preview + handles double-click-to-configure.
			defaults: {
				icon: LAYER_ICONS.chart,
				droppable: false,
				editable: false,
				resizable: false,
				selectable: true,
				hoverable: true,
				layerable: false,
				draggable: false,
				removable: false,
				copyable: false,
				traits: [],
				// NO STYLE SECTORS, matching `g-chart-stored`. Selecting a chart is redirected to its
				// frame, so the rail does not target this today — but it was the one chart-shaped type
				// offering all seven sectors, Typography included, which is the sector deliberately
				// hidden on the frame because it cannot reach a word the renderer draws. A latent
				// inconsistency that would bite the first time anything selects the chart directly.
				noStyle: true,
				unstylable: showOnlySectors()
			},
			// Target-bar label from the chart type: "Table" for data tables, else "Chart — Bar" etc.
			getName(this: any): string {
				const t =
					((this.get('attributes') as Record<string, string>) ?? {})['data-comp-type'] ??
					'line_chart';
				if (t === 'data_table') return 'Table';
				const labels: Record<string, string> = {
					line_chart: 'Line',
					bar_chart: 'Bar',
					pie_chart: 'Pie',
					donut_chart: 'Donut',
					stacked_area_chart: 'Stacked area'
				};
				return `Chart — ${labels[t] ?? 'Chart'}`;
			},
			init(this: any) {
				const lockComp = (c: any) => {
					c.set({
						selectable: false,
						hoverable: false,
						draggable: false,
						removable: false,
						copyable: false,
						highlightable: false
					});
					c.get('components')?.each(lockComp);
				};
				this.get('components')?.each(lockComp);
				this.listenTo(this.get('components'), 'add', lockComp);
			},
			toHTML(this: any): string {
				const attrs = (this.get('attributes') as Record<string, string>) ?? {};
				const styles = (this.get('style') as Record<string, string>) ?? {};
				const compId = attrs['data-comp-id'] || 'comp_0';
				const compType = attrs['data-comp-type'] ?? '';
				const dataSource = attrs['data-comp-data-source'] ?? '';
				// Manual data (data-comp-inline-data present) emits the marker even with no dataset — the
				// worker reads the rows from the component's binding. Keyed off the data's presence, not
				// the source-mode flag, to match serialize.ts (#7).
				const hasManual = !!attrs['data-comp-inline-data'];
				if (!dataSource && !hasManual) return '';

				const el = (this as any).getEl?.() as HTMLElement | undefined;
				const renderedW = el?.offsetWidth;
				const renderedH = el?.offsetHeight;

				const containerW = styles.width
					? `width:${styles.width};max-width:100%`
					: renderedW
						? `width:${renderedW}px;max-width:100%`
						: 'width:100%';

				// MEASURED px FIRST, exactly as `data-width` below — and for the same reason, which this
				// branch did not follow. `styles.height` is the AUTHORED CSS string, and `auto`, `100%`
				// and `12.5rem` are not drawing dimensions: `components.py::_size` refuses to read digits
				// out of them and falls back to the canonical 340. So a chart in a box with no pixel
				// height was drawn at 340 however tall the author made it — it grew on the canvas and
				// stopped at a fixed height in the document, which reads as a chart that ignores its own
				// box. The authored string stays as the fallback for a component not yet laid out.
				const isPieLike = compType === 'pie_chart' || compType === 'donut_chart';
				const containerHVal = isPieLike
					? `${Math.min(renderedW ?? 360, 360)}px`
					: renderedH
						? `${renderedH}px`
						: styles.height
							? styles.height
							: '300px';
				const h = ` data-height="${containerHVal}"`;

				if (compType === 'data_table') {
					const cols = attrs['data-comp-columns'] ?? '';
					const hdrs = attrs['data-comp-headers'] ?? '';
					const hBg = attrs['data-comp-header-bg'] ?? '#1e3a5f';
					const hFg = attrs['data-comp-header-fg'] ?? '#ffffff';
					const striped = attrs['data-comp-striped'] ?? 'true';
					const title = attrs['data-comp-title'] ?? '';
					const w = styles.width
						? ` data-width="${styles.width}"`
						: renderedW
							? ` data-width="${renderedW}px"`
							: '';
					return `<g-table data-id="${compId}" data-source="${dataSource}" data-columns="${cols}" data-headers="${hdrs}" data-header-bg="${hBg}" data-header-fg="${hFg}" data-striped="${striped}" data-title="${title}"${w}${h} />`;
				}

				// `data-width` — which a CHART never sent, so the worker fell back to a canonical 600 for
				// every one of them and then had to fit that shape into whatever box the author drew,
				// leaving empty bands on every chart. THIS is the fix for that, and the reason nothing
				// needs stretching: with the real box through, the chart is LAID OUT at it — a taller box
				// is a longer y axis (charts.py::_frame). MEASURED px, never the authored CSS string
				// first — `100%`, `auto` and `12.5rem` are not drawing dimensions, and the worker falls
				// back to canonical rather than reading digits out of them (`components.py::_size`).
				const wAttr = renderedW
					? ` data-width="${renderedW}px"`
					: styles.width
						? ` data-width="${styles.width}"`
						: '';
				return (
					`<div style="${containerW};height:${containerHVal};overflow:hidden;">` +
					`<g-chart data-id="${compId}"${wAttr}${h} />` +
					`</div>`
				);
			}
		},
		view: {
			// Double-click opens the config modal (the "double-click to configure" affordance). The
			// modal itself lives in TemplateEditor, so bridge via a custom editor event carrying the
			// component. `events` augments the default component view events (select/drag stay intact).
			events: { dblclick: 'onDblClick' },
			onDblClick(this: any, e: Event) {
				e.preventDefault();
				e.stopPropagation();
				editor.trigger('chart:configure', this.model);
			},
			// Re-render when the config (data-comp-* attributes) changes, so the preview + caption
			// reflect the latest configuration immediately after Apply (addAttributes alone only
			// updates DOM attributes, not the rendered body).
			init(this: any) {
				this.listenTo(this.model, 'change:attributes', this.render);
				// AND on the frame's own style, because that is what a resize writes. Without it the
				// chart kept the axis it was drawn with and the SVG merely scaled to the new box — the
				// very mismatch measuring exists to remove — until some attribute happened to change.
				const frame = this.model.parent?.();
				if (frame) this.listenTo(frame, 'change:style', this.render);
			},
			onRender(this: any) {
				const el = this.el as HTMLElement;
				const attrs = (this.model.get('attributes') as Record<string, string>) ?? {};
				const type = attrs['data-comp-type'] ?? 'line_chart';
				// The box is MEASURED, in two passes, and that is the only way to be sure the drawing
				// fills it. A viewBox that is even slightly the wrong shape is letterboxed by
				// `preserveAspectRatio="meet"` — correct behaviour, and visible as bands down the sides
				// of a chart that should have filled its card. So the shell goes in first, its chart
				// slot is measured, and the SVG is drawn at exactly that. `toHTML` exports this
				// element's own offset size, and the slot is that size too (the card frames itself with
				// an inset OUTLINE, which costs no layout), so the canvas and the document agree.
				const hBg = attrs['data-comp-header-bg'] ?? '#1e3a5f';
				const hFg = attrs['data-comp-header-fg'] ?? '#ffffff';
				// Render the chart chrome into an inner box that fills the element (height:100% +
				// box-sizing), so the surrounding chart-frame's padding shows as space around the
				// framed chart. Element styling lives on the frame, not here.
				el.innerHTML =
					// FILLS THE HOST AND ASSERTS NO FLOOR OF ITS OWN. The 210px minimum lives on the host
					// instead (`grapesjs-canvas.css`), because a floor here made the card taller than the
					// element containing it and the next block in the document rode up over the chart's
					// bottom edge. `toHTML` measures the HOST back out as `data-height`, so the floor is
					// still what the PDF renders an unsized chart at.
					// NO BACKGROUND of its own: the CHART paints its own (`charts.py::_card_bg`, set in the
					// Appearance panel), and an opaque card here would hide whatever the author chose.
					// An inset OUTLINE rather than a border, the same reason the editor's rails use one: a
					// border takes 2px out of the content box, so the chart slot would be a different
					// shape from the box `toHTML` exports and the drawing would be letterboxed inside it.
					`<div style="position:relative;height:100%;min-height:0;box-sizing:border-box;` +
					`border-radius:6px;outline:1px solid #e5e7eb;outline-offset:-1px;` +
					`overflow:hidden;display:flex">` +
					// NO GROUND OF ITS OWN. The slot is transparent, so what shows behind the chart is the
					// background the chart itself paints — which is exactly what the PDF will show.
					// It carried a translucent wash to make the footprint visible on a white page, and the
					// cost was that the editor and the export disagreed: every chart read as a grey slab
					// whatever background had been set, so the one thing this preview exists to answer —
					// "what will this look like" — was answered wrongly. The frame's own border and the
					// binding strip already say where the chart lands.
					`<div data-chart-slot style="flex:1;min-width:0;min-height:0;overflow:hidden;` +
					`display:flex"></div>` +
					chartConfigCaption(attrs) +
					`</div>`;

				// PASS TWO: the slot now has a real box, so the drawing can be made to fit it exactly.
				// Falls back to the worker's own defaults if the element is not laid out yet — a first
				// mount, where a re-render follows anyway.
				const slot = el.querySelector('[data-chart-slot]') as HTMLElement | null;
				if (!slot) return;
				const draw = () => {
					const w = Math.round(slot.clientWidth) || 600;
					const h = Math.round(slot.clientHeight) || 340;
					// Same box, nothing to redraw — and this is what stops the observer's own initial
					// callback re-rendering the drawing it was just handed.
					if (w === this._chartW && h === this._chartH) return;
					this._chartW = w;
					this._chartH = h;
					slot.innerHTML = chartPreviewHtml(type, hBg, hFg, attrs, undefined, w, h);
				};
				this._chartW = 0;
				this._chartH = 0;
				draw();

				// MEASURED WHENEVER THE BOX MOVES, not only when the frame's style does. The drawing is
				// laid out AT its box — a taller box is a longer y axis, not the same picture stretched
				// — so a chart drawn at one shape and shown at another is letterboxed by
				// `preserveAspectRatio="meet"`, which is the white margin down the side of a chart that
				// should have filled its card. `change:style` catches a resize the author commits
				// through the rail or the handles, and misses every other way the box can change: a
				// width in `%` when the column beside it changes, a page that reflows, a font that
				// lands late. The observer catches all of them, including the ones being dragged.
				this._chartRo?.disconnect();
				if (typeof ResizeObserver !== 'undefined') {
					this._chartRo = new ResizeObserver(draw);
					this._chartRo.observe(slot);
				}
			},
			removed(this: any) {
				// The element is going away and the observer holds a reference to it.
				this._chartRo?.disconnect();
				this._chartRo = undefined;
			}
		}
	});

	// ── chart-frame — the single selectable/stylable wrapper around a locked chart. It's what the
	//    user sees as "the chart component": one layer item, click anywhere selects it, and its own
	//    padding/background/border frame the chart (and export to the PDF via toHTML). The inner
	//    chart is locked + non-layerable (see the chart type above), so there's no nested Section /
	//    Column / Chart tree — just one "Chart" item. ──
	dc.addType('chart-frame', {
		isComponent: (el: HTMLElement) =>
			el.tagName === 'DIV' && el.dataset?.gjsType === 'chart-frame'
				? { type: 'chart-frame' }
				: false,
		model: {
			defaults: {
				name: 'Chart',
				icon: LAYER_ICONS.chart,
				droppable: false,
				editable: false,
				resizable: true,
				selectable: true,
				hoverable: true,
				traits: [],
				// The frame owns the padding and border around the chart. Keep Size & Space, Borders and
				// Effects; hide Position; from Layout keep display + wrap.
				//
				// NO BACKGROUND. It is the chart's own now, set in its Appearance panel and painted by
				// the renderer onto the figure itself (`charts.py::_card_bg`). Leaving it here as well
				// would be two controls for one colour, one of them a whole panel away from the chart —
				// and they would layer rather than agree.
				//
				// NO TYPOGRAPHY. The frame holds no text of its own — every word in a chart is drawn by
				// the chart (axis ticks, the legend, data labels), and a font set on the wrapper does not
				// reach any of them. So the sector offered controls that changed nothing visible, which
				// reads as a chart that ignores its own font. Chart text is set in the chart's own
				// appearance configuration, where the renderer can actually apply it.
				// `float` only, NOT `display`. The frame holds exactly one locked chart that has to fill
				// it, which is why the canvas stylesheet makes the frame a flex column — so `display`
				// here is a control whose only reachable outcomes are "no change" and "break the fill".
				// `float` is real: it is how text wraps around a chart.
				unstylable: [
					...hideSectors('position', 'typography', 'background'),
					...sectorPropsExcept('layout', 'float')
				]
			},
			getName() {
				return 'Chart';
			},
			toHTML(this: any, opts?: any): string {
				// Serialise the frame so its padding/background/border wrap the chart's `<g-chart>` marker
				// in the exported document.
				//
				// THE ID AND THE CLASSES ARE THE POINT, and their absence is why NOTHING an author set on
				// a chart frame reached the PDF. GrapesJS 0.23 defaults `avoidInlineStyle: true`, so a
				// style set from the rail is written as a CSS RULE keyed `#<id>` (or `.<class>`) and
				// `get('style')` stays EMPTY. `serialize.ts` saves `getCss()`, so the rule WAS stored —
				// against a div that carried no id for it to match. The canvas showed the background
				// because the canvas DOM has the id; the document was unstyled, and it read as "you can
				// set a background and it stays transparent". The inline read stays for anything stored
				// before this, so both sources are honoured.
				let inner = '';
				this.get('components')?.each?.((c: any) => {
					inner += c.toHTML?.(opts) ?? '';
				});
				return chartFrameHtml({
					id: this.getId?.(),
					classes: (this.getClasses?.() ?? []) as string[],
					style: (this.get('style') as Record<string, string>) ?? {},
					inner
				});
			}
		}
	});

	// ── template header/footer chrome ──
	for (const [type, tag] of [
		['tmpl-footer', 'footer'],
		['tmpl-header', 'header']
	] as const) {
		const cls = type === 'tmpl-footer' ? 'tmpl-footer' : 'tmpl-header';
		const icon = type === 'tmpl-footer' ? LAYER_ICONS.footer : LAYER_ICONS.header;
		const label = type === 'tmpl-footer' ? 'Footer' : 'Header';
		dc.addType(type, {
			isComponent: (el: Element) => (el as HTMLElement).classList?.contains(cls),
			model: {
				defaults: {
					icon,
					tagName: tag,
					draggable: false,
					copyable: false,
					selectable: true,
					hoverable: true,
					removable: true,
					droppable: false,
					// Keep Size/Background/Borders/Typography/Effects; hide Layout + Position.
					unstylable: hideSectors('layout', 'position')
				},
				getName() {
					return label;
				}
			}
		});
	}

	// ── editor-label — the non-exported helper headers inside repeat/conditional blocks
	//    (the "Repeat — choose a collection" / branch labels). They carry data-editor-label /
	//    data-branch-label and are excluded from toHTML; without a type they fell to the
	//    base type and were accidentally clickable. Lock them out of selection entirely. ──
	dc.addType('editor-label', {
		isComponent: (el: Element) =>
			(el as HTMLElement).getAttribute?.('data-editor-label') != null ||
			(el as HTMLElement).getAttribute?.('data-branch-label') != null
				? { type: 'editor-label' }
				: false,
		model: {
			defaults: {
				selectable: false,
				hoverable: false,
				highlightable: false,
				draggable: false,
				droppable: false,
				copyable: false,
				removable: false,
				layerable: false,
				editable: false
			}
		}
	});
}
