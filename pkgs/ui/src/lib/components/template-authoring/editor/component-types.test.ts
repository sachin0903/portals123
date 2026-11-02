import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';
import {
	buildJTemplate,
	buildTextTableHtml,
	chartConfigCaption,
	chartFrameHtml,
	chartPreviewHtml,
	datasetLabel,
	defaultJDesign,
	DOC_JTABLE_CSS,
	JINJA_FILTERS,
	jtemplateToEditorHtml,
	parseJTemplate,
	sampleTableRows,
	tablePreviewInner
} from './component-types';
import { DEFAULT_PALETTE, renderChartSvg } from './chart-preview';
import { applyJDesign, jtemplateToEditorHtml as toEditor, wrapperHtml } from './component-types';

describe('buildTextTableHtml', () => {
	it('builds a header row + body grid of editable text cells', () => {
		const html = buildTextTableHtml(3, 2, defaultJDesign());
		// A styled Jinja-table-compatible <table> so text + data tables look identical.
		expect(html).toContain('<table class="doc-jtable-inner"');
		// 2 header cells, all RTE-editable (data-gjs-type=text).
		expect((html.match(/<th data-gjs-type="text">/g) ?? []).length).toBe(2);
		// 3 rows × 2 columns = 6 body cells.
		expect((html.match(/<td data-gjs-type="text">/g) ?? []).length).toBe(6);
		expect(html).toContain('<thead>');
		expect(html).toContain('<tbody>');
	});

	it('clamps to at least one row and one column', () => {
		const html = buildTextTableHtml(0, 0, defaultJDesign());
		expect((html.match(/<th /g) ?? []).length).toBe(1);
		expect((html.match(/<td /g) ?? []).length).toBe(1);
	});

	it('carries the design CSS variables onto the table tag', () => {
		const html = buildTextTableHtml(1, 1, { ...defaultJDesign(), headerBg: '#123456' });
		expect(html).toContain('--jt-header-bg:#123456');
	});
});

describe('sampleTableRows', () => {
	it('produces n rows, one cell per column, keyed off the base field type', () => {
		const rows = sampleTableRows(['row.fund_name', 'row.market_value', 'row.allocation_pct'], 3);
		expect(rows).toHaveLength(3);
		expect(rows[0]).toHaveLength(3);
		expect(rows[0][0]).toBe('Global Equity'); // name-ish → label
		expect(rows[0][1]).toMatch(/^£[\d,]+\.\d{2}$/); // value-ish → currency, formatGBP's 2dp
		expect(rows[0][2]).toMatch(/%$/); // pct-ish → percentage
	});

	it('falls back to a generic sample for unrecognised fields', () => {
		expect(sampleTableRows(['row.foo'], 1)[0][0]).toBe('Sample 1');
	});
});

describe('chartConfigCaption', () => {
	it('summarises a cartesian chart: type, title, source and x/y fields', () => {
		const html = chartConfigCaption({
			'data-comp-type': 'bar_chart',
			'data-comp-title': 'Growth',
			'data-comp-data-source': 'holdings',
			'data-comp-x-field': 'year',
			'data-comp-y-field': 'value'
		});
		// Asserting the FACTS are shown, not how they are laid out: the strip's job is that an
		// author can read what this component is bound to without opening its settings. Pinning the
		// exact string froze the presentation and made a restyle look like a regression.
		for (const fact of ['Bar chart', 'Growth', 'holdings', 'year', 'value']) {
			expect(html, fact).toContain(fact);
		}
		// The axis a field is mapped to is part of the fact — `year` alone does not say which axis.
		expect(html).toContain('X');
		expect(html).toContain('Y');
	});

	it('uses label/value for pie charts', () => {
		const html = chartConfigCaption({
			'data-comp-type': 'pie_chart',
			'data-comp-data-source': 'alloc',
			'data-comp-label-field': 'asset',
			'data-comp-value-field': 'amount'
		});
		for (const fact of ['Pie chart', 'Label', 'asset', 'Value', 'amount']) {
			expect(html, fact).toContain(fact);
		}
	});

	it('shows an unset field as a dash rather than omitting it', () => {
		// An unfinished binding has to be VISIBLE. Dropping the pill entirely made a half-configured
		// chart look complete in the canvas, and the blank only appeared in the generated PDF.
		const html = chartConfigCaption({
			'data-comp-type': 'bar_chart',
			'data-comp-data-source': 'holdings',
			'data-comp-x-field': 'year'
			// no y field
		});
		expect(html).toContain('year');
		expect(html).toContain('—');
	});

	it('prompts to configure when no data source is set, and escapes values', () => {
		const none = chartConfigCaption({ 'data-comp-type': 'line_chart' });
		expect(none).toContain('No data source');
		const escaped = chartConfigCaption({
			'data-comp-type': 'data_table',
			'data-comp-data-source': 'a & b'
		});
		expect(escaped).toContain('a &amp; b');
	});
});

describe('tablePreviewInner', () => {
	it('renders a design-styled table with escaped headers + body cells', () => {
		const html = tablePreviewInner(
			{ ...defaultJDesign(), headerBg: 'transparent' },
			['A & B', 'C'],
			[['1', '2']]
		);
		expect(html).toContain('class="doc-jtable-inner');
		expect(html).toContain('--jt-header-bg:transparent'); // transparent header flows through
		expect(html).toContain('--jt-row-bg:transparent'); // row background (default transparent)
		expect(html).toContain('--jt-body-fg:#374151'); // body text colour
		expect(html).toContain('<th>A &amp; B</th>'); // header HTML-escaped
		expect((html.match(/<td>/g) ?? []).length).toBe(2);
	});
});

describe('the filter menu names only filters that render', () => {
	// An unregistered filter is not a blank in the PDF — it is a TemplateAssertionError raised days
	// after the document looked fine on the canvas, at the moment a client is waiting for it. So this
	// reads jinja_engine.py directly (the API container ships without this app), the same cross-language
	// pin the email side's field-filters.test.ts keeps on the send environment.
	// fileURLToPath, not `.pathname` — on Windows the latter yields `/C:/…`, which `join` mangles.
	const REPO_ROOT = fileURLToPath(new URL('../../../../../../../', import.meta.url));
	const engine = readFileSync(
		join(REPO_ROOT, 'apps', 'api', 'app', 'render', 'jinja_engine.py'),
		'utf8'
	);
	const registered = new Set(
		[...engine.matchAll(/env\.filters\["([a-z_]+)"\]/g)].map((m) => m[1] as string)
	);

	// Jinja's own filters, which the engine never registers because it never has to. Listing them
	// here is what keeps this test a real check rather than a rubber stamp: a NEW menu entry that is
	// neither registered nor on this list fails, and adding to this list is a deliberate act with
	// the null handling documented on JINJA_FILTERS to answer for.
	const JINJA_BUILTINS = new Set([
		'int',
		'abs',
		'round',
		'upper',
		'title',
		'capitalize',
		'default'
	]);

	const base = (snippet: string): string => snippet.split('(')[0].trim();

	it('found the registry to check against', () => {
		// Guards against the regex silently matching nothing — an empty `registered` would pass the
		// assertion below for every filter in the menu, including a name the engine never registers.
		expect(registered.size).toBeGreaterThan(5);
		expect(registered.has('currency')).toBe(true);
	});

	it('offers no filter the render engine cannot resolve', () => {
		const unresolvable = JINJA_FILTERS.map((f) => base(f.snippet)).filter(
			(name) => !registered.has(name) && !JINJA_BUILTINS.has(name)
		);
		expect(unresolvable).toEqual([]);
	});

	it('spells the platform filters exactly as the engine registers them', () => {
		// The engine registers `percentage` and `number`. `percent` and `format_number` are neither
		// registered nor Jinja builtins, so either spelling is an unrenderable template — named
		// explicitly so a rename on the Python side cannot quietly reinstate one.
		const snippets = JINJA_FILTERS.map((f) => base(f.snippet));
		expect(snippets).toContain('percentage');
		expect(snippets).toContain('number');
		expect(snippets).not.toContain('percent');
		expect(snippets).not.toContain('format_number');
	});

	it('names each filter once, so the picker cannot offer two spellings of one behaviour', () => {
		const snippets = JINJA_FILTERS.map((f) => base(f.snippet));
		expect(new Set(snippets).size).toBe(snippets.length);
	});
});

// ── the data table's empty state ─────────────────────────────────────────────
describe('a data table with no rows', () => {
	const cols = [
		{ header: 'Date', expr: 'row.date' },
		{ header: 'Amount', expr: 'row.amount' }
	];

	it('emits an else branch so a header never floats over blank space', () => {
		// The failure this fixes: with no `{% else %}`, an empty dataset rendered the headings and
		// nothing beneath — indistinguishable from a broken binding, and the commonest way a
		// generated document looked wrong.
		const tpl = buildJTemplate('book_valuations', cols);
		expect(tpl).toContain('{% else %}');
		expect(tpl).toContain('colspan="2"');
		expect(tpl).toContain('No valuations to show');
		// The else row must carry no token: `parseJTemplate` finds columns by `<td>{{ … }}</td>`,
		// so one here would be read as a third column.
		const elseBranch = tpl.slice(tpl.indexOf('{% else %}'), tpl.indexOf('{% endfor %}'));
		expect(elseBranch).not.toContain('{{');
	});

	it('names the dataset the way the PDF renderer does', () => {
		// Mirrors app/render/empty_state.py::label_for_code — one fact, one phrasing.
		expect(datasetLabel('book_valuations')).toBe('valuations');
		expect(datasetLabel('book_contributions')).toBe('contributions');
		expect(datasetLabel('client_holdings')).toBe('client holdings');
		expect(datasetLabel('')).toBe('data');
	});

	it('round-trips through parseJTemplate with the else branch present', () => {
		const tpl = buildJTemplate('book_valuations', cols);
		const parsed = parseJTemplate(tpl);
		expect(parsed?.dataset).toBe('book_valuations');
		expect(parsed?.columns.map((c) => c.expr)).toEqual(['row.date', 'row.amount']);
		expect(parsed?.columns.map((c) => c.header)).toEqual(['Date', 'Amount']);
	});

	it('round-trips even when the empty row carries a token', () => {
		// The regex used to terminate only on `endfor`, so the lazy capture ran past the data row
		// and swallowed the empty one. It extracted the right columns only because the empty row
		// happens to hold no token — luck, not a contract. This is that luck removed.
		const tpl =
			'<table class="doc-jtable-inner"><thead><tr><th>A</th><th>B</th></tr></thead>' +
			'<tbody>{% for row in book_valuations %}<tr><td>{{ row.a }}</td><td>{{ row.b }}</td></tr>' +
			'{% else %}<tr><td colspan="2">{{ empty_message }}</td></tr>{% endfor %}</tbody></table>';
		const parsed = parseJTemplate(tpl);
		expect(parsed?.columns.map((c) => c.expr)).toEqual(['row.a', 'row.b']);
	});

	it('shows exactly one row in the editor, not a phantom empty one', () => {
		// The canvas drops the else BRANCH, content included. Stripping only its tags would leave a
		// "No … to show" row in the author's table that they can neither edit nor delete.
		const html = jtemplateToEditorHtml(buildJTemplate('book_valuations', cols));
		expect(html).not.toContain('No valuations to show');
		expect(html).not.toContain('jt-empty');
		// One body row, still carrying the author's cells.
		expect(html.match(/<tr>/g)?.length).toBe(2); // header + the one data row
		expect(html).toContain('row.date');
	});

	it('styles the empty row in both stylesheets', () => {
		// DOC_JTABLE_CSS ships in the export; the canvas copy is a separate file. The module's own
		// rule is that table styling lives in both and they change together.
		expect(DOC_JTABLE_CSS).toContain('jt-empty');
	});
});

// ── the canvas preview ──────────────────────────────────────────────────────
describe('the chart preview on the canvas', () => {
	const BINDING = {
		'data-comp-x-field': 'product',
		'data-comp-y-field': 'market_value'
	};
	const mark = (type: string, attrs: Record<string, string> = BINDING) =>
		chartPreviewHtml(type, '#123456', '#abcdef', { ...attrs, 'data-comp-type': type });

	it('draws the real chart, by the real renderer', () => {
		// The canvas answers "what will this look like", so it runs the SAME renderer as the drawer's
		// preview and the worker's charts.py — not a picture of a chart and not an abstract mark. What
		// this replaced twice: a hand-drawn chart over invented specifics, then a glyph that was
		// honest and mute.
		const bar = mark('bar_chart');
		expect(bar).toContain('<svg');
		expect(bar).toContain('<rect');
		expect(mark('line_chart')).toContain('<polyline');
		expect(
			mark('pie_chart', {
				'data-comp-label-field': 'product',
				'data-comp-value-field': 'market_value'
			})
		).toContain('<path');
	});

	it('takes its sample values from the bound field NAMES', () => {
		// `sampleTableRows` — the very generator the canvas table preview uses — keys off the field
		// name, so a money field yields money and a name field yields a name. That is what makes the
		// axes plausible without inventing anything about the client.
		//
		// The invariant is that the values FOLLOW THE BINDING. What must not come back is a fixed cast
		// of specifics independent of it — the hand-drawn chart this replaced labelled its bars Q1–Q5
		// whatever the chart was bound to, which an author reads as data.
		const money = mark('bar_chart', {
			'data-comp-x-field': 'fund_name',
			'data-comp-y-field': 'market_value'
		});
		const other = mark('bar_chart', {
			'data-comp-x-field': 'detail',
			'data-comp-y-field': 'quantity'
		});
		expect(money).not.toBe(other);
		// Nothing hardcodes a quarter, a year or a currency figure into the picture.
		expect(money).not.toContain('Q1');
		expect(other).not.toContain('Q1');
	});

	it('honours the bound fields, so the picture tracks the binding', () => {
		// The axis is labelled from the DATA, so a chart bound to nothing must not look complete.
		const bound = mark('bar_chart');
		const unbound = chartPreviewHtml('bar_chart', '#123456', '#abcdef', {
			'data-comp-type': 'bar_chart'
		});
		expect(bound).not.toBe(unbound);
		// The renderer's own empty state names what is missing, rather than drawing a plausible chart.
		expect(unbound).toContain('stroke-dasharray');
	});

	it('draws a table from its columns, in its header colours', () => {
		const html = chartPreviewHtml('data_table', '#123456', '#abcdef', {
			'data-comp-type': 'data_table',
			'data-comp-columns': 'date,amount',
			'data-comp-headers': 'Date,Amount'
		});
		expect(html).toContain('<table');
		expect(html).toContain('<th>Date</th>');
		expect(html).toContain('#123456'); // the header colours the drawer offers
		// A table with no columns says so rather than drawing an empty box.
		const bare = chartPreviewHtml('data_table', '#123456', '#abcdef', {
			'data-comp-type': 'data_table'
		});
		expect(bare).toContain('Choose columns');
	});

	it('falls back to the palette the worker draws with', () => {
		// A preview's whole job is to claim something true about the output. Nothing writes a firm's
		// CSS-derived palette into the component and the worker knows nothing of it, so defaulting to
		// one showed a branded tenant colours the PDF would never use. `DEFAULT_PALETTE` is kept equal
		// to charts.py's by `chart-preview.contract.test.ts`.
		expect(mark('bar_chart')).toContain(DEFAULT_PALETTE[0]);
		// An explicit choice still wins — that one IS in the component and the worker honours it.
		expect(mark('bar_chart', { ...BINDING, 'data-comp-colors': '#ff00ff' })).toContain('#ff00ff');
	});

	it('gives every type its own picture', () => {
		// A preview that does not change is worse than none: it reports the wrong choice as made.
		const types = [
			'line_chart',
			'bar_chart',
			'pie_chart',
			'donut_chart',
			'stacked_area_chart',
			'data_table'
		];
		const attrs = {
			...BINDING,
			'data-comp-label-field': 'product',
			'data-comp-value-field': 'market_value',
			'data-comp-columns': 'date,amount'
		};
		expect(new Set(types.map((t) => mark(t, attrs))).size).toBe(types.length);
	});
});

// ── the exported chart frame ─────────────────────────────────────────────────
describe('the exported chart frame', () => {
	it('carries the component id', () => {
		// THE DEFECT: GrapesJS 0.23 defaults `avoidInlineStyle: true`, so a background set from the
		// style rail becomes a CSS rule keyed `#<id>` and the component's own `style` stays empty.
		// `serialize.ts` saved that rule, against a div with no id — so the canvas showed the colour
		// and the PDF was unstyled, which reads as "you can set a background and it stays
		// transparent". Nothing about a background is special here: EVERY style on a frame was lost.
		expect(chartFrameHtml({ id: 'i3kb' })).toContain('id="i3kb"');
	});

	it('carries the classes, which a rule can also be keyed on', () => {
		expect(chartFrameHtml({ classes: ['figure', 'wide'] })).toContain('class="figure wide"');
		// No empty attribute when there is nothing to say.
		expect(chartFrameHtml({})).not.toContain('class=');
		expect(chartFrameHtml({})).not.toContain('id=');
	});

	it('still writes an inline style, for anything stored before the rule era', () => {
		const html = chartFrameHtml({ style: { padding: '12px', background: 'red' } });
		expect(html).toContain('style="padding:12px;background:red"');
	});

	it('drops a blank declaration rather than emitting a dangling colon', () => {
		expect(chartFrameHtml({ style: { padding: '', background: 'red' } })).toContain(
			'style="background:red"'
		);
	});

	it('escapes a quote in an attribute value', () => {
		// `escHtml` is for text and leaves `"` alone. A font stack carries one, and unescaped it ends
		// the attribute early and spills the rest of the declaration into the tag.
		const html = chartFrameHtml({ style: { 'font-family': '"Helvetica Neue",sans-serif' } });
		expect(html).toContain('&quot;Helvetica Neue&quot;');
		expect(html.match(/style="/g)?.length).toBe(1);
	});

	it('keeps the marker and its children', () => {
		const html = chartFrameHtml({ id: 'x', inner: '<g-chart data-id="c1" />' });
		expect(html).toContain('data-gjs-type="chart-frame"');
		expect(html).toContain('<g-chart data-id="c1" />');
		expect(html.startsWith('<div ')).toBe(true);
		expect(html.endsWith('</div>')).toBe(true);
	});
});

// ── a retired chart type ─────────────────────────────────────────────────────
describe('the donut chart', () => {
	const EDITOR = fileURLToPath(new URL('../TemplateEditor.svelte', import.meta.url));

	it('is no longer offered', () => {
		const src = readFileSync(EDITOR, 'utf8');
		const list = /const CHART_TYPES = \[([\s\S]*?)\] as const;/.exec(src);
		expect(list, 'CHART_TYPES not found — update this test rather than deleting it').not.toBeNull();
		expect(list![1]).not.toContain('donut_chart');
		// The list still offers something, so an empty match cannot pass the assertion above.
		expect(list![1]).toContain('line_chart');
	});

	it('is still RENDERED, so an existing template does not change', () => {
		// Retiring a type is a decision about what to OFFER, never about what to honour. A template
		// already built on a donut must keep rendering exactly as it did — deleting it from the
		// renderers would silently change a published document.
		const svg = renderChartSvg(
			{
				type: 'donut_chart',
				props: {},
				binding: { label_field: 'product', value_field: 'market_value' }
			},
			[
				{ product: 'A', market_value: 10 },
				{ product: 'B', market_value: 20 }
			],
			300,
			300
		);
		expect(svg).toContain('<path');
		// A ring, not wedges under a cover disc — the hole shows the frame's background through it.
		expect(svg).toContain('stroke-width=');
		expect(svg).not.toContain('fill="#fff"');
	});
});

// ── per-column layout ───────────────────────────────────────────────────────
describe('a table preview with per-column layout', () => {
	const design = defaultJDesign();
	const headers = ['A', 'B', 'C'];
	const rows = [['x', '1', '2']];

	it('mirrors tables.py: a colgroup, and a fixed layout only when a width is set', () => {
		// The editor and the worker build this table twice, in two languages. What must not differ is
		// the RULE — a declared width does nothing under `auto`, and imposing `fixed` on a table with
		// no widths would stop its columns sizing to their content.
		const sized = tablePreviewInner(design, headers, rows, [
			{ width: '50%' },
			{},
			{ width: '25%' }
		]);
		expect(sized).toContain('<colgroup>');
		expect(sized).toContain('width:50%');
		expect(sized).toContain('<col/>');
		expect(sized).toContain('table-layout:fixed');
	});

	it('leaves a table with no widths exactly as it was', () => {
		// The jtable preview passes no columns at all, so its output must not change.
		const plain = tablePreviewInner(design, headers, rows);
		expect(plain).not.toContain('colgroup');
		expect(plain).not.toContain('table-layout:fixed');
		expect(plain).toBe(tablePreviewInner(design, headers, rows, [{}, {}, {}]));
	});

	it('aligns the header and the body together', () => {
		const html = tablePreviewInner(design, headers, rows, [
			{ align: 'left' },
			{ align: 'right' },
			{ align: 'center' }
		]);
		expect((html.match(/text-align:right/g) ?? []).length).toBe(2); // the th and its td
		expect(html).toContain('text-align:center');
		// `left` is the default and is not written, so it cannot fight an authored stylesheet.
		expect(html).not.toContain('text-align:left');
	});

	it('wraps a long unbroken value rather than overflowing its cell', () => {
		// A fixed layout will not widen a column for a long value — an ISIN or a reference would
		// spill out of it.
		expect(tablePreviewInner(design, headers, rows, [{ width: '10%' }])).toContain(
			'word-wrap:break-word'
		);
	});
});

// ── a jtable's per-column width, end to end ─────────────────────────────────
describe("a jtable column's width", () => {
	const cols = [
		{ header: 'Fund', expr: 'row.fund_name', width: '60', align: 'left' },
		{ header: 'Value', expr: 'row.value', width: '40', align: 'right' }
	];

	it('is written into the template as a colgroup, with a fixed layout', () => {
		const tpl = buildJTemplate('holdings', cols);
		expect(tpl).toContain('<colgroup>');
		expect(tpl).toContain('width:60%');
		expect(tpl).toContain('table-layout:fixed');
		expect(tpl).toContain('text-align:right');
	});

	it('round-trips, so editing anything else does not drop it', () => {
		// The dialog rebuilds the template from its column list, so a setting the parser cannot read
		// back is lost the next time a header is typed.
		const parsed = parseJTemplate(buildJTemplate('holdings', cols));
		expect(parsed?.columns.map((c) => c.width)).toEqual(['60', '40']);
		expect(parsed?.columns.map((c) => c.align)).toEqual(['', 'right']);
	});

	it('SURVIVES applyJDesign — the defect that made every width do nothing', () => {
		// `applyJDesign` REPLACES the whole opening <table> tag to bake the design in, which
		// discarded `table-layout:fixed` on every export and every canvas render. Under `auto` a
		// declared column width is only a suggestion the engine overrides to fit the longest cell, so
		// the widths were stored correctly, round-tripped correctly, and changed nothing on the page.
		const design = defaultJDesign();
		const exported = applyJDesign(buildJTemplate('holdings', cols), design);
		expect(exported).toContain('table-layout:fixed');
		expect(exported).toContain('width:60%'); // the colgroup is a child, so it always survived
		expect(exported).toContain('--jt-header-bg'); // the design is still baked in
		// The canvas takes the same path, which is why the editor showed no change either.
		expect(applyJDesign(toEditor(buildJTemplate('holdings', cols)), design)).toContain(
			'table-layout:fixed'
		);
	});

	it('does not impose a fixed layout on a table with no widths', () => {
		// `auto` sizes columns to their content and is the better layout when nobody has asked for
		// anything — and it must not be forced on every table that already exists.
		const plain = buildJTemplate('holdings', [{ header: 'Fund', expr: 'row.fund_name' }]);
		expect(plain).not.toContain('colgroup');
		expect(applyJDesign(plain, defaultJDesign())).not.toContain('table-layout:fixed');
	});

	it('clamps a width and treats junk as automatic', () => {
		const tpl = buildJTemplate('holdings', [
			{ header: 'A', expr: 'row.a', width: '500' },
			{ header: 'B', expr: 'row.b', width: 'abc' }
		]);
		expect(tpl).toContain('width:100%');
		expect(tpl).not.toContain('abc');
		expect(tpl).toContain('<col/>'); // the junk one is automatic
	});
});

// ── every wrapper with a custom toHTML ──────────────────────────────────────
describe('wrapperHtml', () => {
	// THE DEFECT ALL THREE WRAPPERS HAD: a hand-written `toHTML` emitting only `data-gjs-type`
	// silently discards the component's own id, classes and style. GrapesJS 0.23 defaults
	// `avoidInlineStyle: true`, so a style set from the rail is a CSS rule keyed `#<id>` — drop the
	// id and the rule matches nothing. For the two TABLES that meant a width the author set was
	// thrown away on export, the inner table's `width:100%` filled the page, and a table could not
	// be made narrower than its container by any means available in the editor.
	it('carries the id, the classes and the style', () => {
		const html = wrapperHtml('doc-jtable', {
			id: 'i9',
			classes: ['wide'],
			style: { width: '60%' }
		});
		expect(html).toContain('data-gjs-type="doc-jtable"');
		expect(html).toContain('id="i9"');
		expect(html).toContain('class="wide"');
		expect(html).toContain('style="width:60%"');
	});

	it('emits a type its own data attributes verbatim', () => {
		// The tables round-trip their design through one, so reopening a saved template restores the
		// panel. Losing it would not lose the look — that is baked into the table tag — but it would
		// lose the ability to edit it.
		expect(wrapperHtml('doc-texttable', { data: { 'data-jdesign': 'abc%20def' } })).toContain(
			'data-jdesign="abc%20def"'
		);
	});

	it('says nothing it has nothing to say about', () => {
		expect(wrapperHtml('doc-jtable')).toBe('<div data-gjs-type="doc-jtable"></div>');
	});

	it('drops a blank declaration rather than emitting a dangling colon', () => {
		expect(wrapperHtml('x', { style: { width: '', color: 'red' } })).toContain('style="color:red"');
	});

	it('escapes a quote in an attribute value', () => {
		const html = wrapperHtml('x', { style: { 'font-family': '"Helvetica Neue",sans-serif' } });
		expect(html).toContain('&quot;Helvetica Neue&quot;');
		expect(html.match(/style="/g)?.length).toBe(1);
	});

	it('is the one implementation — the chart frame goes through it', () => {
		expect(chartFrameHtml({ id: 'c1', inner: '<g-chart/>' })).toBe(
			wrapperHtml('chart-frame', { id: 'c1', inner: '<g-chart/>' })
		);
	});
});
