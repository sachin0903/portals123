/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Page layout settings — paper size, orientation and margins, applied to the
 * canvas by injecting a <style> override into the canvas iframe document (the
 * base sizes live in grapesjs-canvas.css; this overrides them live). Mirrors the
 * prototype's Page settings panel.
 */

export type PageSizeKey = 'A4' | 'A5' | 'A3' | 'Letter' | 'Legal';
export type Orientation = 'portrait' | 'landscape';

/** Paper dimensions in millimetres (portrait). */
export const PAGE_DIMS: Record<PageSizeKey, { w: number; h: number }> = {
	A4: { w: 210, h: 297 },
	A5: { w: 148, h: 210 },
	A3: { w: 297, h: 420 },
	Letter: { w: 216, h: 279 },
	Legal: { w: 216, h: 356 }
};

export interface PageLayout {
	size: PageSizeKey;
	orientation: Orientation;
	/** Margins in mm: [top, right, bottom, left]. */
	margins: [number, number, number, number];
}

export function defaultLayout(): PageLayout {
	return { size: 'A4', orientation: 'portrait', margins: [19, 19, 19, 19] };
}

/** width/height in mm for a layout, accounting for orientation. */
export function pageDimensions(layout: PageLayout): { w: number; h: number } {
	const base = PAGE_DIMS[layout.size];
	return layout.orientation === 'landscape' ? { w: base.h, h: base.w } : { w: base.w, h: base.h };
}

import { getPageComponents } from './pages';

const STYLE_ID = 'gjs-page-layout-overrides';

/** A page component's own layout, read from its size/orientation/margin traits. */
export function layoutFromComp(comp: any): PageLayout {
	const d = defaultLayout();
	if (!comp) return d;
	return {
		size: (comp.get('pageSize') as PageSizeKey) ?? d.size,
		orientation: (comp.get('pageOrientation') as Orientation) ?? d.orientation,
		margins: [
			Number(comp.get('marginTop') ?? d.margins[0]),
			Number(comp.get('marginRight') ?? d.margins[1]),
			Number(comp.get('marginBottom') ?? d.margins[2]),
			Number(comp.get('marginLeft') ?? d.margins[3])
		]
	};
}

/** Size each .page from its OWN doc-page traits, so pages can differ (e.g. a landscape
    chart page among portrait pages). Sizing is applied inline per element (with !important
    to beat the base grapesjs-canvas.css, whose link GrapesJS injects after our override).
    Geometry comes from each page's own traits, so no layout argument is needed. */
export function applyPageLayout(editor: any): void {
	try {
		const doc = editor.Canvas?.getDocument?.() as Document | null;
		if (!doc) return;
		const comps = getPageComponents(editor);
		const els = Array.from(doc.querySelectorAll('.page')) as HTMLElement[];
		const dims = els.map((_, i) => pageDimensions(layoutFromComp(comps[i])));
		// Body fits the widest page; each page centres within it (margin auto) so a narrower
		// page isn't left-aligned. Transparent bg so the grey canvas shows between the sheets.
		const maxW = dims.reduce((m, d) => Math.max(m, d.w), 0);
		let styleEl = doc.getElementById(STYLE_ID) as HTMLStyleElement | null;
		if (!styleEl) {
			styleEl = doc.createElement('style');
			styleEl.id = STYLE_ID;
			doc.head.appendChild(styleEl);
		}
		// Size the body to the widest page and centre it: an inline-block body under an html that
		// text-align:centres it stays centred at ANY zoom even when wider than the viewport (a
		// landscape sheet), whereas flex `align-items:center` would clip the overflow off-centre.
		// Asserted here (runtime-appended, so latest source order) with !important so it wins over
		// the base grapesjs-canvas.css rules and any GrapesJS-injected iframe styles.
		styleEl.textContent =
			`html { display: block !important; text-align: center !important; }` +
			`body { width: ${maxW}mm !important; background: transparent !important;` +
			` display: inline-block !important; vertical-align: top !important; text-align: left !important; }`;
		els.forEach((el, i) => {
			const lay = layoutFromComp(comps[i]);
			const { w, h } = pageDimensions(lay);
			const [t, r, b, l] = lay.margins;
			el.style.setProperty('width', `${w}mm`, 'important');
			el.style.setProperty('min-height', `${h}mm`, 'important');
			el.style.setProperty('padding', `${t}mm ${r}mm ${b}mm ${l}mm`, 'important');
			el.style.setProperty('margin-left', 'auto', 'important');
			el.style.setProperty('margin-right', 'auto', 'important');
			el.style.setProperty('box-sizing', 'border-box', 'important');
		});
	} catch {
		/* canvas not ready */
	}
}

/** Merge inline style onto a page component (per-page background / border). Empty
    values clear that property. */
export function setPageStyle(editor: any, index: number, style: Record<string, string>): void {
	try {
		const comp = getPageComponents(editor)[index];
		if (!comp) return;
		const attrs = (comp.get('attributes') as Record<string, string>) ?? {};
		const map: Record<string, string> = {};
		(attrs.style ?? '').split(';').forEach((rule) => {
			const i = rule.indexOf(':');
			if (i > 0) map[rule.slice(0, i).trim()] = rule.slice(i + 1).trim();
		});
		Object.assign(map, style);
		const next = Object.entries(map)
			.filter(([, v]) => v)
			.map(([k, v]) => `${k}:${v}`)
			.join(';');
		comp.addAttributes({ style: next });
	} catch {
		/* component not ready */
	}
}

/** Read a page component's inline style as a property map (for reflecting per-page
    background / border back into the panel). */
export function getPageStyle(editor: any, index: number): Record<string, string> {
	try {
		const comp = getPageComponents(editor)[index];
		if (!comp) return {};
		const attrs = (comp.get('attributes') as Record<string, string>) ?? {};
		const map: Record<string, string> = {};
		(attrs.style ?? '').split(';').forEach((rule) => {
			const i = rule.indexOf(':');
			if (i > 0) map[rule.slice(0, i).trim()] = rule.slice(i + 1).trim();
		});
		return map;
	} catch {
		return {};
	}
}

const HF_STYLE_ID = 'gjs-hf-overrides';

type HFColumn = { content: string; align: 'left' | 'center' | 'right'; fr: number };
type HFDef = {
	enabled: boolean;
	columns: HFColumn[];
	color: string;
	borderColor: string;
	background: string;
	fontSize: string;
	paddingH: string;
	paddingV: string;
};

function makeHFElement(doc: Document, def: HFDef, position: 'top' | 'bottom'): HTMLElement {
	const el = doc.createElement('div');
	el.className = position === 'top' ? 'gjs-hf-header' : 'gjs-hf-footer';
	const pv = def.paddingV || '8px';
	const ph = def.paddingH || '72px';
	const border = def.borderColor || '#e5e7eb';
	const borderRule =
		position === 'bottom' ? `border-top:1px solid ${border}` : `border-bottom:1px solid ${border}`;
	const cols =
		def.columns?.length > 0 ? def.columns : [{ content: '', align: 'left' as const, fr: 1 }];
	const gridTemplate = cols.map((c) => `${c.fr || 1}fr`).join(' ');
	el.style.cssText = [
		`position:absolute`,
		`${position}:0`,
		`left:0`,
		`right:0`,
		`display:grid`,
		`grid-template-columns:${gridTemplate}`,
		`align-items:center`,
		`padding:${pv} ${ph}`,
		`font-size:${def.fontSize || '12px'}`,
		`color:${def.color || '#6b7280'}`,
		borderRule,
		`background:${def.background || 'transparent'}`,
		`pointer-events:none`,
		`user-select:none`,
		`z-index:10`
	].join(';');
	el.innerHTML = cols
		.map((c) => `<span style="text-align:${c.align}">${c.content || ''}</span>`)
		.join('');
	return el;
}

/** Inject/remove per-page header and footer overlay divs in the canvas iframe.
    Elements are raw DOM (not GrapesJS components) so the editor won't select them.
    headers/footers are indexed by page; an undefined or disabled entry means no overlay for that page. */
export function applyHeaderFooter(editor: any, headers: HFDef[], footers: HFDef[]): void {
	try {
		const doc = editor.Canvas?.getDocument?.() as Document | null;
		if (!doc) return;

		doc.querySelectorAll('.gjs-hf-header, .gjs-hf-footer').forEach((el) => el.remove());

		const hasAny = headers.some((h) => h?.enabled) || footers.some((f) => f?.enabled);
		let styleEl = doc.getElementById(HF_STYLE_ID) as HTMLStyleElement | null;
		if (!styleEl) {
			styleEl = doc.createElement('style');
			styleEl.id = HF_STYLE_ID;
			doc.head.appendChild(styleEl);
		}
		styleEl.textContent = hasAny ? '.page { position: relative !important; }' : '';

		doc.querySelectorAll('.page').forEach((page, i) => {
			const header = headers[i];
			const footer = footers[i];
			if (header?.enabled) page.prepend(makeHFElement(doc, header, 'top'));
			if (footer?.enabled) page.appendChild(makeHFElement(doc, footer, 'bottom'));
		});
	} catch {
		/* canvas not ready */
	}
}

/** Canvas zoom — CSS `zoom` is layout-aware so the scroll area shrinks with the
    content (unlike `transform: scale`). We also write the value directly into
    GrapesJS's internal model attribute (without triggering the change event that
    would apply a conflicting `transform: scale`) so GrapesJS compensates correctly
    when computing hover / selection overlay positions. */
export function applyZoom(editor: any, percent: number): void {
	try {
		// Register the zoom factor so GrapesJS scales overlay coordinates correctly.
		// Writing to .attributes directly skips the Backbone change event that would
		// otherwise apply a transform: scale() and fight our CSS zoom below.
		if (editor.Canvas?.model) {
			editor.Canvas.model.attributes.zoom = percent;
		}
		const frames = document.querySelector('.gjs-cv-canvas__frames') as HTMLElement | null;
		if (frames) {
			frames.style.transform = 'none';
			frames.style.zoom = percent === 100 ? '' : String(percent / 100);
		}
	} catch {
		/* canvas not ready */
	}
}
