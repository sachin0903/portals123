/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Block palette — the chart placeholder + its `data-comp-*` contract and the block content are
 * ported from the reference so the editor, the `chart` component type's toHTML, and the Python
 * renderer all agree. Categories/order: Basic · Charts · Tables · Logic. Charts expose one
 * configurable Chart (type/dataset/fields set in the right panel), dropped inside a chart-frame;
 * Tables expose a Text table (static) and a Data table (a {% for %} loop over a dataset).
 */
import type { VarSchema } from './types';

/** The chart placeholder dropped on the canvas. Only the element's data-comp-* contract and a
    default min-height matter — its visible chrome (preview + config caption) is rendered by the
    chart component view's onRender, which leaves the element's own inline style to the style
    manager so Layout/Size (padding, margin, width, alignment) actually apply. */
export function makeChartPlaceholder(type: string): string {
	return (
		`<div data-gjs-type="chart" data-comp-id="comp_NEW" data-comp-type="${type}"` +
		` data-comp-data-source="" data-comp-x-field="" data-comp-y-field=""` +
		` data-comp-label-field="" data-comp-value-field=""` +
		` data-comp-title="" data-comp-x-label="" data-comp-y-label=""` +
		`></div>`
	);
}

// ── layout skeleton helpers ──
// Columns are just plain <div>s in a flex row — no special gjs-cell/doc-col component types (they're
// divs, so we don't complicate them). Each column carries a min-height so an empty column is visible
// and a valid drop target; the row lays them out with a gap.
const _skCol = (w: string) => `<div style="flex:0 0 ${w};width:${w};min-height:48px"></div>`;
const _skSec = (...ws: string[]) =>
	`<div style="display:flex;gap:16px;width:100%">${ws.map(_skCol).join('')}</div>`;

const icon = (inner: string) =>
	`<svg viewBox="0 0 24 14" fill="none" xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;

// String category 'Basic' matches the grapesjs-blocks-basic plugin's category (set in
// create.ts) so Text/Image land in the same group as our Basic blocks.
const BASIC = 'Basic';
const CHARTS = { id: 'charts', label: 'Charts', open: true };
const TABLES = { id: 'tables', label: 'Tables', open: true };
// Domain ("smart") components — pre-wired to the record's merge fields so an author doesn't hand-token a
// Label+Value grid each time. Letter = identity/adviser/firm furniture; Regulatory = risk/disclaimer copy.
const LETTER = { id: 'letter', label: 'Letter', open: true };
const REGULATORY = { id: 'regulatory', label: 'Regulatory', open: true };

// A Label+Value row (doc-field): typed label + a value cell that can hold merge tokens. `stacked` puts
// the value under the label. Used to compose the identity/adviser/firm cards without hand-building divs.
const _field = (label: string, value: string, stacked = false) =>
	`<div data-gjs-type="doc-field" data-layout="${stacked ? 'stacked' : 'inline'}" ` +
	`style="display:flex;${stacked ? 'flex-direction:column;' : 'align-items:baseline;'}gap:${stacked ? '2px' : '8px'};margin:4px 0;font-size:13px">` +
	`<div data-gjs-type="text" style="font-weight:600;${stacked ? 'font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.03em' : 'min-width:130px'}">${label}</div>` +
	`<div data-gjs-type="text" data-tmpl-ph style="flex:1">${value}</div></div>`;

export function registerBlocks(
	editor: any,
	getSchema: () => VarSchema,
	brandLogo: string | null = null
): void {
	const bm = editor.BlockManager;
	const add = (id: string, def: Record<string, unknown>) => bm.add(id, def);
	// The tenant's real logo, HTML-attribute-escaped for the Letterhead's <img src>. Empty → the
	// image renders as a placeholder the author can fill.
	const logoSrc = (brandLogo ?? '').replace(/"/g, '&quot;');

	// ── Basic (Text + Image come from the grapesjs-blocks-basic plugin) ──
	add('typo-label-value', {
		label: 'Label + Value',
		category: BASIC,
		media: icon(
			'<rect x="1" y="1.5" width="8" height="2.5" rx=".75" fill="currentColor" opacity=".5"/><rect x="11" y="1.5" width="12" height="2.5" rx=".75" fill="currentColor"/><rect x="1" y="6.5" width="8" height="2.5" rx=".75" fill="currentColor" opacity=".5"/><rect x="11" y="6.5" width="10" height="2.5" rx=".75" fill="currentColor"/>'
		),
		// A doc-field (see component-types.ts): a typed label+value row with an inline/stacked
		// Layout trait. Label + value are editable text cells (data-gjs-type=text).
		content: `<div data-gjs-type="doc-field" data-layout="inline" style="display:flex;gap:8px;align-items:baseline;margin:6px 0;font-size:13px"><div data-gjs-type="text" style="font-weight:600;min-width:140px">Label</div><div data-gjs-type="text" data-tmpl-ph style="flex:1">Value</div></div>`
	});
	add('typo-bullet-list', {
		label: 'Bullet List',
		category: BASIC,
		media: icon(
			'<circle cx="2.5" cy="3.5" r="1.5" fill="currentColor"/><rect x="6" y="2.5" width="16" height="2" rx=".75" fill="currentColor"/><circle cx="2.5" cy="8" r="1.5" fill="currentColor"/><rect x="6" y="7" width="13" height="2" rx=".75" fill="currentColor"/>'
		),
		content: `<ul style="display:inline-block;width:100%;margin:0 0 12px;padding-left:20px;font-size:14px;line-height:1.6"><li data-tmpl-ph style="margin-bottom:4px">First item</li><li data-tmpl-ph style="margin-bottom:4px">Second item</li><li data-tmpl-ph style="margin-bottom:4px">Third item</li></ul>`
	});
	add('typo-numbered-list', {
		label: 'Numbered List',
		category: BASIC,
		media: icon(
			'<rect x="1" y="1.5" width="3" height="3" rx=".5" fill="currentColor" opacity=".7"/><rect x="6" y="2.5" width="16" height="2" rx=".75" fill="currentColor"/><rect x="1" y="6.5" width="3" height="3" rx=".5" fill="currentColor" opacity=".7"/><rect x="6" y="7.5" width="13" height="2" rx=".75" fill="currentColor"/>'
		),
		content: `<ol style="display:inline-block;width:100%;margin:0 0 12px;padding-left:20px;font-size:14px;line-height:1.6"><li data-tmpl-ph style="margin-bottom:4px">First item</li><li data-tmpl-ph style="margin-bottom:4px">Second item</li><li data-tmpl-ph style="margin-bottom:4px">Third item</li></ol>`
	});
	// Note / Highlight are the two variants of the doc-callout type (registered in
	// component-types.ts) — dropped as a typed element so they keep their identity and
	// expose the Variant/Accent traits, instead of the old bare <div>s.
	add('container-note-box', {
		label: 'Note Box',
		category: BASIC,
		media: icon(
			'<rect x="1" y="1" width="2.5" height="12" rx=".75" fill="currentColor"/><rect x="5" y="2" width="18" height="2.5" rx=".75" fill="currentColor" opacity=".8"/><rect x="5" y="6.5" width="15" height="1.5" rx=".5" fill="currentColor" opacity=".4"/>'
		),
		content: `<div data-gjs-type="doc-callout" data-variant="note" data-accent="#3b82f6" data-tmpl-ph style="font-size:12px;color:#374151;line-height:1.6">Add note content here.</div>`
	});
	add('container-highlight', {
		label: 'Highlight Box',
		category: BASIC,
		media: icon(
			'<rect x=".5" y=".5" width="23" height="13" rx="2" fill="currentColor" opacity=".1"/><rect x=".5" y=".5" width="23" height="13" rx="2" stroke="currentColor" stroke-width=".8"/><rect x="2.5" y="3" width="12" height="2.5" rx=".75" fill="currentColor" opacity=".7"/>'
		),
		content: `<div data-gjs-type="doc-callout" data-variant="highlight" data-accent="#3b82f6" data-tmpl-ph style="font-size:14px;color:#1e3a8a;line-height:1.6">Highlight content here.</div>`
	});
	// NO Divider, Columns or Add Layout block. A divider is a rule you SET on something (the
	// doc-callout and doc-field types already carry their own), and the two multi-column skeletons
	// dropped an empty 50/50 or 30/70 row that an author then had to fill — structure placed before
	// there was anything to put in it. They are applied from the right-hand rail to a selection
	// instead, so the palette stays what you ADD. `doc-divider` stays registered in
	// component-types.ts so templates already holding one still load and render.
	add('sec-full', {
		label: 'Full Width',
		category: BASIC,
		media: icon(
			'<rect x="1" y="1" width="22" height="12" rx="1" fill="none" stroke="currentColor" stroke-width="1.3"/>'
		),
		content: _skSec('100%')
	});

	// ── Charts — one configurable Chart; type (line/bar/pie/donut/stacked), dataset and field
	//    bindings are set in the right panel (see TemplateEditor CHART_TYPES). Dropped inside a
	//    `chart-frame` wrapper so it's a single selectable/one-layer unit whose padding/background/
	//    border frame the chart (the chart itself is locked inside — see component-types.ts). ──
	add('chart', {
		label: 'Chart',
		category: CHARTS,
		media: icon(
			'<rect x="2" y="7" width="3.5" height="6" rx=".5" fill="currentColor" opacity=".6"/><rect x="8" y="3.5" width="3.5" height="9.5" rx=".5" fill="currentColor"/><rect x="14" y="6" width="3.5" height="7" rx=".5" fill="currentColor" opacity=".6"/>'
		),
		// A REAL HEIGHT, not a hidden floor. A dropped chart used to arrive carrying
		// `min-height:200px` — a constraint the author never set, shown back to them as though they
		// had, and a second number competing with the Height they did set. It also left the frame's
		// height AUTO, the one case a percentage height cannot resolve against, so the chart sat on
		// its floor while the frame grew around it.
		//
		// 340px because that is what `components.py::_size` renders an unsized chart at, so the box on
		// the canvas and the box in the PDF are the same number from the moment it lands. It is an
		// ordinary style: the Height control shows it, and changing it changes the chart.
		content: `<div data-gjs-type="chart-frame" style="height:340px">${makeChartPlaceholder('line_chart')}</div>`
	});

	// ── Tables — two kinds:
	//    • Text table (doc-texttable): a static grid the author types into directly.
	//    • Data table (doc-jtable): a real {% for %} loop over a dataset — visual (dataset +
	//      columns) or raw (jinja/HTML) editing in the right panel.
	//    Both share the JDesign styling; the g-table marker path + its presets were removed. ──
	const tableMedia = icon(
		'<rect x="2" y="2" width="20" height="3.5" rx="1" fill="currentColor"/><rect x="2" y="6.5" width="20" height="2.5" fill="currentColor" opacity=".4"/><rect x="2" y="10" width="20" height="2.5" fill="currentColor" opacity=".25"/>'
	);
	// Text-table icon: a filled grid (rows + a column split) to distinguish it from the
	// data table's "header + repeating rows" glyph.
	const textTableMedia = icon(
		'<rect x="2" y="2" width="20" height="11" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/><line x1="2" y1="6" x2="22" y2="6" stroke="currentColor" stroke-width="1.2"/><line x1="2" y1="9.5" x2="22" y2="9.5" stroke="currentColor" stroke-width="1.2"/><line x1="12" y1="2" x2="12" y2="13" stroke="currentColor" stroke-width="1.2"/>'
	);
	add('table-text', {
		label: 'Text table',
		category: TABLES,
		media: textTableMedia,
		content: `<div data-gjs-type="doc-texttable" data-rows="3" data-cols="2"></div>`
	});
	const jtableCols = JSON.stringify([
		{ header: 'Fund', expr: 'row.fund_name' },
		{ header: 'Allocation', expr: 'row.allocation' },
		{ header: 'Value', expr: 'row.value' }
	]);
	add('table-jinja', {
		label: 'Data table',
		category: TABLES,
		media: tableMedia,
		content: `<div data-gjs-type="doc-jtable" data-dataset="holdings" data-columns='${jtableCols}'></div>`
	});
	// TWO TABLES, and only two: a text one and a data one. "Projection" was a THIRD entry that was
	// not a third kind — the same `doc-jtable`, pre-bound to a dataset with four illustration
	// columns — so the palette offered a choice where there was none and an author had to know that
	// picking it was the same as picking Data table and rebinding. A preset is not a component.
	// Nothing is lost: bind a data table to `projections` and type the columns, which is what the
	// block did on the author's behalf.

	// NO Logic blocks in the palette. Repeat and Conditional were dragged in from here and then
	// configured on the right ("choose a collection in the panel →" was literally printed inside
	// them), which is the wrong way round: they wrap content that already exists rather than being
	// content you place. They are applied from the right-hand rail to the selection instead, so the
	// palette stays what you ADD and the rail stays what you SET.

	// ── Letter — identity / adviser / firm furniture, pre-bound to merge fields ──
	const idIcon = icon(
		'<circle cx="6" cy="4.5" r="2.5" fill="currentColor"/><path d="M1.5 12.5c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4" stroke="currentColor" stroke-width="1.3" fill="none"/><rect x="14" y="3" width="9" height="2" rx="1" fill="currentColor" opacity=".5"/><rect x="14" y="7" width="7" height="2" rx="1" fill="currentColor" opacity=".5"/>'
	);
	add('letter-letterhead', {
		label: 'Letterhead',
		category: LETTER,
		media: icon(
			'<rect x="1" y="2" width="13" height="2.5" rx="1" fill="currentColor"/><rect x="1" y="6" width="9" height="1.5" rx=".75" fill="currentColor" opacity=".5"/><rect x="17" y="2" width="6" height="5" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><line x1="1" y1="11" x2="23" y2="11" stroke="currentColor" stroke-width="1.2"/>'
		),
		content:
			`<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;border-bottom:2px solid #1e3a5f;padding-bottom:12px;margin-bottom:20px">` +
			`<div><div data-gjs-type="text" style="font-size:20px;font-weight:700;color:#1e3a5f">{{ firm.name }}</div>` +
			`<div data-gjs-type="text" style="font-size:11px;color:#64748b;margin-top:4px">Authorised and regulated by the Financial Conduct Authority. FRN {{ firm.frn }}.</div></div>` +
			`<img data-gjs-type="image" src="${logoSrc}" alt="Firm logo" style="max-height:48px;max-width:160px;object-fit:contain"/>` +
			`</div>`
	});
	add('letter-recipient', {
		label: 'Recipient address',
		category: LETTER,
		media: idIcon,
		content:
			`<div style="margin:16px 0;font-size:13px;line-height:1.5">` +
			`<div data-gjs-type="text">{{ client.title }} {{ client.name }}</div>` +
			`<div data-gjs-type="text" data-tmpl-ph style="color:#374151">{{ client.address }}</div>` +
			`</div>`
	});
	add('letter-salutation', {
		label: 'Salutation & sign-off',
		category: LETTER,
		media: icon(
			'<rect x="1" y="2" width="12" height="2" rx="1" fill="currentColor"/><rect x="1" y="6" width="18" height="1.5" rx=".75" fill="currentColor" opacity=".4"/><rect x="1" y="9.5" width="8" height="2" rx="1" fill="currentColor"/>'
		),
		content:
			`<div style="margin:12px 0;font-size:13px;line-height:1.6">` +
			`<div data-gjs-type="text" style="margin-bottom:12px">Dear {{ client.first_name }},</div>` +
			`<div data-gjs-type="text" data-tmpl-ph style="margin-bottom:24px">Write your letter here.</div>` +
			`<div data-gjs-type="text">Yours sincerely,</div>` +
			`<img data-gjs-type="image" alt="Signature" style="max-height:44px;max-width:180px;object-fit:contain;margin:8px 0"/>` +
			`<div data-gjs-type="text" style="font-weight:600">{{ adviser.name }}</div>` +
			`</div>`
	});
	add('letter-client-identity', {
		label: 'Client details',
		category: LETTER,
		media: idIcon,
		content:
			`<div style="margin:12px 0">` +
			_field('Name', '{{ client.name }}') +
			_field('Date of birth', '{{ client.date_of_birth }}') +
			_field('Address', '{{ client.address }}') +
			_field('Email', '{{ client.email }}') +
			_field('Mobile', '{{ client.phone }}') +
			_field('Phone', '{{ client.telephone }}') +
			`</div>`
	});
	add('letter-adviser', {
		label: 'Adviser details',
		category: LETTER,
		media: idIcon,
		content:
			`<div style="margin:12px 0">` +
			_field('Adviser', '{{ adviser.name }}') +
			_field('Email', '{{ adviser.email }}') +
			`</div>`
	});
	add('letter-meta', {
		label: 'Document meta',
		category: LETTER,
		media: icon(
			'<rect x="1" y="4" width="6" height="2" rx="1" fill="currentColor"/><rect x="9" y="4" width="5" height="2" rx="1" fill="currentColor" opacity=".6"/><rect x="16" y="4" width="7" height="2" rx="1" fill="currentColor" opacity=".4"/>'
		),
		content:
			`<div data-gjs-type="text" data-tmpl-ph style="font-size:11px;color:#64748b;margin:8px 0;letter-spacing:.02em">` +
			`{{ document_name }} · {{ date }} · Prepared for {{ client.name }} by {{ adviser.name }}` +
			`</div>`
	});

	// ── Regulatory — risk/disclaimer copy (mostly boilerplate + a variant trait) ──
	add('reg-risk-warning', {
		label: 'Risk warning',
		category: REGULATORY,
		media: icon(
			'<path d="M8 2 L14 12 H2 Z" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linejoin="round"/><line x1="8" y1="6" x2="8" y2="8.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="8" cy="10.3" r=".7" fill="currentColor"/><rect x="17" y="4" width="6" height="1.5" rx=".75" fill="currentColor" opacity=".4"/><rect x="17" y="7.5" width="4" height="1.5" rx=".75" fill="currentColor" opacity=".4"/>'
		),
		content:
			`<div data-gjs-type="doc-risk-warning" data-variant="investment" style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px 12px;margin:10px 0;font-size:11px;color:#9a3412;line-height:1.5">` +
			'The value of investments and the income from them can fall as well as rise. You may get back ' +
			'less than you invested. Past performance is not a reliable indicator of future results.' +
			`</div>`
	});
	add('reg-fscs', {
		label: 'FSCS statement',
		category: REGULATORY,
		media: icon(
			'<path d="M8 1.5 L14 4 V8 C14 11 8 13 8 13 C8 13 2 11 2 8 V4 Z" stroke="currentColor" stroke-width="1.2" fill="none"/><path d="M5.5 7 L7.2 8.7 L10.5 5.4" stroke="currentColor" stroke-width="1.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
		),
		content:
			`<div data-gjs-type="text" data-tmpl-ph style="font-size:11px;color:#475569;line-height:1.5;margin:10px 0">` +
			'Your eligible deposits and investments may be protected up to the applicable limit by the ' +
			'Financial Services Compensation Scheme (FSCS). The level of cover depends on the type of ' +
			'product and the provider. For more information visit www.fscs.org.uk.' +
			`</div>`
	});
	add('reg-complaints', {
		label: 'Complaints footer',
		category: REGULATORY,
		media: icon(
			'<rect x="1" y="2" width="16" height="8" rx="1.5" stroke="currentColor" stroke-width="1.2" fill="none"/><path d="M4 10 L4 13 L8 10" stroke="currentColor" stroke-width="1.2" fill="none" stroke-linejoin="round"/><line x1="4" y1="5" x2="14" y2="5" stroke="currentColor" stroke-width="1.2"/><line x1="4" y1="7.5" x2="11" y2="7.5" stroke="currentColor" stroke-width="1.2" opacity=".5"/>'
		),
		content:
			`<div data-gjs-type="text" data-tmpl-ph style="font-size:11px;color:#64748b;line-height:1.5;margin:10px 0;border-top:1px solid #e2e8f0;padding-top:8px">` +
			'If you wish to complain about any aspect of our service, please contact {{ firm.name }}. ' +
			'If you are not satisfied with our response, you may refer your complaint to the Financial ' +
			'Ombudsman Service (www.financial-ombudsman.org.uk).' +
			`</div>`
	});

	void getSchema; // reserved for future schema-driven blocks (palette-aware variants)
}
