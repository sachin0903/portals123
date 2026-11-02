/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Save-time serialization — turn the GrapesJS document into the persisted
 * `TemplateSchema` (html + css + typed components + datasets + gjs_project).
 * Ported from the reference (`scanComponents`/`stripOrphanedCss`/
 * `applyInlineConditions`/`unwrapJinjaBlocks` + the save payload assembly).
 */
import type { ComponentDef, TemplateSchema, VarSchema } from './types';
import { pageDimensions, layoutFromComp, type PageLayout } from './page-settings';
import { getPageComponents } from './pages';
import { DOC_JTABLE_CSS } from './component-types';

/** Drop CSS id-rules whose id is no longer in the HTML (charts/tables became markers). */
export function stripOrphanedCss(css: string, html: string): string {
	if (!css || typeof window === 'undefined') return css;
	const presentIds = new Set<string>();
	for (const m of html.matchAll(/\bid="([^"]+)"/g)) presentIds.add(m[1]!);
	return css
		.replace(/#([a-z0-9_-]+)\s*\{([^}]*)\}/gi, (match, id) => (presentIds.has(id) ? match : ''))
		.replace(/\n{3,}/g, '\n\n')
		.trim();
}

/** Unwrap editor-only raw-jinja wrappers. */
function unwrapJinjaBlocks(html: string): string {
	return html.replace(/<div[^>]*data-jinja-raw[^>]*>([\s\S]*?)<\/div>/gi, '$1');
}

/** Turn elements carrying data-comp-cond into {% if %}…{% endif %} wrappers. */
function applyInlineConditions(html: string): string {
	if (typeof window === 'undefined') return html;
	try {
		const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
		const root = doc.body.firstElementChild as HTMLElement;
		const els = Array.from(root.querySelectorAll('[data-comp-cond]')) as HTMLElement[];
		if (!els.length) return html;
		const replacements: [string, string][] = [];
		els.reverse().forEach((el, i) => {
			const expr = el.getAttribute('data-comp-cond') ?? '';
			if (!expr) return;
			el.removeAttribute('data-comp-cond');
			el.removeAttribute('data-comp-cond-config');
			const key = `CONDPLACEHOLDER${i}`;
			replacements.push([key, `{% if ${expr} %}${el.outerHTML}{% endif %}`]);
			el.parentNode?.replaceChild(doc.createComment(key), el);
		});
		let result = root.innerHTML;
		for (const [key, replacement] of replacements)
			result = result.replace(`<!--${key}-->`, replacement);
		return result;
	} catch {
		return html;
	}
}

/** Lift chart/table components out of the canvas into the typed ComponentDef contract. */
export function extractComponents(editor: any): {
	components: ComponentDef[];
	datasetUids: string[];
} {
	const components: ComponentDef[] = [];
	const datasetUidSet = new Set<string>();
	const seen = new Set<string>();

	function visit(comp: any) {
		try {
			const attrs = (comp.get?.('attributes') ?? {}) as Record<string, string>;
			const rawId = attrs['data-comp-id'];
			const id = rawId === 'comp_NEW' ? `comp_auto_${seen.size}` : rawId;
			const type = attrs['data-comp-type'];
			if (id && type && !seen.has(id)) {
				seen.add(id);
				const dataSource = attrs['data-comp-data-source'] ?? '';
				const binding: Record<string, unknown> = {
					_resolved_data: dataSource ? `{{ ${dataSource} }}` : ''
				};
				// Manual data: the component carries its own rows (data-comp-inline-data, a
				// {columns, rows} blob) — keyed off the data's PRESENCE, not the UI source-mode flag,
				// so it holds regardless of whether that flag round-trips. When present it wins: set
				// _inline_rows and blank _resolved_data, so a stale dataset binding can't resolve the
				// wrong side and render blank (#7).
				const inlineRaw = attrs['data-comp-inline-data'];
				if (inlineRaw) {
					try {
						const parsed = JSON.parse(inlineRaw);
						if (
							parsed &&
							Array.isArray(parsed.columns) &&
							Array.isArray(parsed.rows) &&
							parsed.rows.length
						) {
							binding._inline_rows = { columns: parsed.columns, rows: parsed.rows };
							binding._resolved_data = '';
						}
					} catch {
						/* ignore malformed inline data */
					}
				}

				if (type === 'data_table') {
					components.push({
						id,
						type,
						props: {
							title: attrs['data-comp-title'] ?? '',
							columns: attrs['data-comp-columns'] ?? '',
							headers: attrs['data-comp-headers'] ?? '',
							header_bg: attrs['data-comp-header-bg'] ?? '#1e3a5f',
							header_fg: attrs['data-comp-header-fg'] ?? '#ffffff',
							striped: attrs['data-comp-striped'] ?? 'true'
						},
						binding
					});
				} else {
					const isPieLike = type === 'pie_chart' || type === 'donut_chart';
					const isStacked = type === 'stacked_area_chart';
					const xField = attrs['data-comp-x-field'] ?? '';
					const yRaw = attrs['data-comp-y-field'] ?? '';
					const yField = yRaw.includes(',')
						? yRaw
								.split(',')
								.map((s) => s.trim())
								.filter(Boolean)
						: yRaw;
					if (isPieLike) {
						binding.label_field = attrs['data-comp-label-field'] ?? '';
						binding.value_field = attrs['data-comp-value-field'] ?? '';
					} else if (isStacked) {
						binding.x_field = xField;
						binding.series_fields = yRaw.includes(',')
							? yRaw
									.split(',')
									.map((s) => s.trim())
									.filter(Boolean)
							: yRaw
								? [yRaw]
								: [];
					} else {
						binding.x_field = xField;
						binding.y_field = yField;
					}
					const rawColors = attrs['data-comp-colors'] ?? '';
					const colorsArr = rawColors
						? rawColors
								.split(',')
								.map((s) => s.trim())
								.filter(Boolean)
						: [];
					const chartProps: Record<string, unknown> = {
						title: attrs['data-comp-title'] ?? '',
						x_label: attrs['data-comp-x-label'] ?? '',
						y_label: attrs['data-comp-y-label'] ?? '',
						widths: attrs['data-comp-widths'] ?? '',
						aligns: attrs['data-comp-aligns'] ?? '',
						value_format: attrs['data-comp-value-format'] || 'number',
						value_decimals: attrs['data-comp-value-decimals'] ?? '',
						show_values: attrs['data-comp-show-values'] === 'true',
						show_grid: (attrs['data-comp-show-grid'] ?? 'true') !== 'false',
						show_legend: (attrs['data-comp-show-legend'] ?? 'true') !== 'false',
						line_width: parseFloat(attrs['data-comp-line-width'] ?? '1.5'),
						// Typography. Written through EVEN WHEN BLANK, and never defaulted here:
						// `charts.py::_typography` owns every fallback, so a value substituted at this
						// seam would be a choice the author never made — and the preview, which reads
						// the same attributes through `previewCompFromAttrs`, would then be drawing a
						// different chart from the one the worker bakes.
						font_family: attrs['data-comp-font-family'] ?? '',
						font_size: attrs['data-comp-font-size'] ?? '',
						text_color: attrs['data-comp-text-color'] ?? '',
						background: attrs['data-comp-background'] ?? '',
						grid_color: attrs['data-comp-grid-color'] ?? '',
						line_style: attrs['data-comp-line-style'] ?? '',
						show_points: attrs['data-comp-show-points'] ?? '',
						area_fill: attrs['data-comp-area-fill'] ?? '',
						bar_width: attrs['data-comp-bar-width'] ?? '',
						bar_corners: attrs['data-comp-bar-corners'] ?? '',
						ring_width: attrs['data-comp-ring-width'] ?? ''
					};
					if (colorsArr.length > 0) chartProps.colors = colorsArr;
					components.push({ id, type, props: chartProps, binding });
				}
				if (attrs['data-comp-dataset-uid']) datasetUidSet.add(attrs['data-comp-dataset-uid']);
			}
			const children = comp.components?.() ?? comp.get?.('components');
			if (children?.each) children.each(visit);
			else if (Array.isArray(children)) children.forEach(visit);
		} catch {
			/* skip malformed component */
		}
	}

	try {
		editor.DomComponents?.getComponents?.()?.each?.(visit);
	} catch {
		/* editor not ready */
	}
	return { components, datasetUids: Array.from(datasetUidSet) };
}

/** Bake each page's own geometry (paper size + orientation + margins) into the exported HTML/CSS as
 *  named CSS pages, so the rendered PDF and on-screen preview honour per-page settings instead of
 *  applying page one's to everything. Each `.page` div (in document order — the same order as
 *  `getPageComponents`) gets a `tmpl-page-{i}` class bound to a `@page tmplpg{i}` rule with that
 *  page's size; the compound `.page.tmpl-page-{i}` selector also sets its width / min-height /
 *  padding, out-specifying the renderer's global `.page` defaults. WeasyPrint switches page geometry
 *  on the `page:` property; browsers ignore `@page` on screen but honour the width/min-height so the
 *  preview matches. Returns the annotated HTML and the CSS block to append (empty if no pages). */
function bakePerPageGeometry(html: string, layouts: PageLayout[]): { html: string; css: string } {
	if (typeof window === 'undefined' || layouts.length === 0) return { html, css: '' };
	try {
		const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
		const root = doc.body.firstElementChild as HTMLElement;
		const pageEls = Array.from(root.querySelectorAll('.page')) as HTMLElement[];
		if (pageEls.length === 0) return { html, css: '' };
		const rules: string[] = [];
		pageEls.forEach((el, i) => {
			const lay = (layouts[i] ?? layouts[layouts.length - 1])!;
			const { w, h } = pageDimensions(lay);
			const [t, r, b, l] = lay.margins;
			el.classList.add(`tmpl-page-${i}`);
			rules.push(
				`@page tmplpg${i} { size: ${w}mm ${h}mm; margin: 0; }`,
				`.page.tmpl-page-${i} { page: tmplpg${i}; width: ${w}mm; min-height: ${h}mm; ` +
					`padding: ${t}mm ${r}mm ${b}mm ${l}mm; }`
			);
		});
		return { html: root.innerHTML, css: rules.join('\n') };
	} catch {
		return { html, css: '' };
	}
}

/** Guard Jinja tags across the DOMParser round-trips in `buildSchema`. The HTML5 parser
 *  "foster-parents" non-whitespace TEXT sitting directly inside a `<table>`/`<tbody>` out in front
 *  of the table — which strands a `{% for %}`/`{% endfor %}` outside its loop body and breaks Jinja
 *  tables. HTML COMMENTS are never foster-parented, so swap each `{{ }}`/`{% %}` for a comment
 *  placeholder before parsing and swap it back afterwards. */
function makeJinjaGuard(): {
	protect: (html: string) => string;
	restore: (html: string) => string;
} {
	const tokens: string[] = [];
	const TAG = /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/g;
	return {
		protect: (html) =>
			html.replace(TAG, (m) => {
				const i = tokens.length;
				tokens.push(m);
				return `<!--gjinja:${i}-->`;
			}),
		restore: (html) => html.replace(/<!--gjinja:(\d+)-->/g, (_m, i) => tokens[Number(i)] ?? '')
	};
}

/** Assemble the persisted schema payload (the generation spec §5.2 shape). Page geometry is baked
 *  per-page into the HTML/CSS (see `bakePerPageGeometry`); the top-level `pageSize` stays as page
 *  one's dimensions — the renderer's global `@page` fallback for any content outside a `.page`. */
export function buildSchema(editor: any, schema: VarSchema, layout: PageLayout): TemplateSchema {
	const css = (editor.getCss?.({ avoidProtected: true }) as string | undefined) ?? '';
	const cleaned = unwrapJinjaBlocks(editor.getWrapper?.()?.toHTML?.() ?? editor.getHtml?.() ?? '')
		.replace(new RegExp(String.fromCharCode(0x200b), 'g'), '') // strip zero-width cursor-escape sentinels
		.replace(/<span\b[^>]*\bjinja-var\b[^>]*>([\s\S]*?)<\/span>/g, '$1'); // unwrap highlight spans
	// Guard Jinja across the DOM round-trips below (see makeJinjaGuard). applyInlineConditions can
	// itself ADD `{% if %}` from conditions, so protect first, run it, then protect again — then
	// bakePerPageGeometry re-serialises safely and we restore the tags at the end.
	const guard = makeJinjaGuard();
	const rawHtml = guard.protect(applyInlineConditions(guard.protect(cleaned)));

	const { components, datasetUids } = extractComponents(editor);
	const layouts = getPageComponents(editor).map((c) => layoutFromComp(c));
	const perPage = bakePerPageGeometry(rawHtml, layouts);
	const finalHtml = guard.restore(perPage.html);
	const baseCss = stripOrphanedCss(css, finalHtml);
	// Ship the Jinja-table print rules when the document uses one, so the JDesign styling (header
	// colours / borders / striping / alignment — set as --jt-* vars per table) renders in the PDF;
	// editor.getCss() doesn't include the canvas stylesheet those rules live in.
	const tableCss = finalHtml.includes('doc-jtable-inner') ? DOC_JTABLE_CSS : '';
	// Page one's geometry backs the top-level pageSize (the renderer's global @page fallback); the
	// per-page rules above drive each individual page. Fall back to the panel layout if, unusually,
	// the document has no `.page` components at all.
	const { w, h } = pageDimensions(layouts[0] ?? layout);

	return {
		html: finalHtml,
		css: [baseCss, perPage.css, tableCss].filter(Boolean).join('\n'),
		components,
		datasets: [...new Set(datasetUids)],
		calculations: [],
		gjs_project: editor.getProjectData?.() ?? null,
		variable_schema: schema,
		pageSize: { width: `${w}mm`, height: `${h}mm` }
	};
}
