/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Editor factory — dynamic-imports GrapesJS (client-only), runs `grapesjs.init`
 * with the reference's config (the canvas iframe loads the vanilla helper scripts
 * from /static), registers our component types / blocks / RTE actions, wires the
 * chart-id assignment + page-add message handler, and loads initial content.
 *
 * GrapesJS touches `window`, so this must only run in the browser (onMount).
 */
import { registerComponentTypes } from './component-types';
import { registerBlocks } from './blocks';
import { loadInto } from './load';
import { assignPageClass, ensurePageClasses } from './pages';
import { STYLE_SECTORS } from './style-config';
import type { VarSchema } from './types';

/**
 * Nudge a Spectrum colour popover back inside the viewport. Spectrum positions the
 * popover directly below its swatch with no viewport-collision handling, so a swatch
 * low in the scrolled Style panel (e.g. Background colour) opens partly or wholly
 * below the fold — its OK button unreachable. We only intervene when it actually
 * overflows, switching to fixed positioning at a clamped location so Spectrum's own
 * absolute placement is left untouched in the common (fits) case.
 */
function clampColorPickerIntoViewport(el: HTMLElement): void {
	requestAnimationFrame(() => {
		const r = el.getBoundingClientRect();
		if (!r.width || !r.height) return;
		const pad = 8;
		let top = r.top;
		let left = r.left;
		if (r.bottom > window.innerHeight - pad) top = window.innerHeight - r.height - pad;
		if (top < pad) top = pad;
		if (r.right > window.innerWidth - pad) left = window.innerWidth - r.width - pad;
		if (left < pad) left = pad;
		if (Math.round(top) === Math.round(r.top) && Math.round(left) === Math.round(r.left)) return;
		el.style.position = 'fixed';
		el.style.top = `${top}px`;
		el.style.left = `${left}px`;
		el.style.margin = '0';
	});
}

/**
 * Watch the parent document for a Spectrum popover becoming visible (it toggles the
 * `sp-hidden` class) and clamp it into the viewport. Returns a disconnect fn.
 */
function watchColorPickers(): () => void {
	const check = (node: Node) => {
		if (!(node instanceof HTMLElement)) return;
		if (node.classList.contains('sp-container') && !node.classList.contains('sp-hidden')) {
			clampColorPickerIntoViewport(node);
		}
	};
	const observer = new MutationObserver((mutations) => {
		for (const m of mutations) {
			if (m.type === 'attributes') check(m.target);
			else m.addedNodes.forEach(check);
		}
	});
	observer.observe(document.body, {
		childList: true,
		subtree: true,
		attributes: true,
		attributeFilter: ['class']
	});
	return () => observer.disconnect();
}

export interface CreateEditorOpts {
	container: HTMLElement;
	blocksEl: HTMLElement;
	layersEl: HTMLElement;
	traitsEl: HTMLElement;
	stylesEl: HTMLElement;
	getSchema: () => VarSchema;
	/** Resolve a dataset's stored sample rows by code, so a data table (doc-jtable) can paint real
	    rows on the canvas instead of literal {{ row.field }} tokens. Null when unknown/no sample. */
	getDatasetSample?: (code: string) => { columns: string[]; rows: unknown[][] } | null;
	initial: { gjs_project?: unknown; html?: string; css?: string } | null;
	/** The tenant's public logo URL, baked into the Letterhead block so it drops the firm's real
	    logo (in the editor and the generated PDF). Null for an unbranded tenant → an empty image
	    placeholder the author can fill. */
	brandLogo?: string | null;
	/** View-only mode for a principal without `templates:write`. GrapesJS has no first-class
	    read-only, so we lock the document down (see applyReadOnly) rather than relying on the host
	    component to hide every affordance. */
	readOnly?: boolean;
}

/**
 * Lock a GrapesJS editor into view-only. GrapesJS ships no read-only mode, so with the /edit route
 * reachable by a `templates:read`-only principal (their overview button says "View"), gating only
 * save/autosave leaves the canvas fully interactive — blocks still drop, the context menu still
 * duplicates/deletes, text still edits. This neutralises the document itself: every component is made
 * non-editable / non-movable / non-droppable / non-removable, the mutating keymaps are stripped, and
 * any late-added component is locked too. Applied on init and on every load (restored content).
 */
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
	// Defence in depth: neutralise anything that still manages to get added.
	editor.on('component:add', (c: any) =>
		c?.set?.(
			{ editable: false, draggable: false, droppable: false, removable: false, copyable: false },
			{ avoidStore: true }
		)
	);
	// Drop the keyboard shortcuts that mutate (delete/backspace/copy/paste/undo/redo/duplicate).
	editor.Keymaps?.removeAll?.();
}

/** The LightningCSS-safe stylesheet load (the reference injects /grapesjs.css as a
    static <link> because Vite/LightningCSS rejects GrapesJS's IE filter syntax), plus
    the parent-document layout overrides — light canvas, centred page, zeroed panel
    gutters (we use our own rails), and a sensible frame height. Ported verbatim. */
function ensureEditorStyles(): void {
	if (!document.getElementById('gjs-css')) {
		const link = document.createElement('link');
		link.id = 'gjs-css';
		link.rel = 'stylesheet';
		link.href = '/grapesjs.css';
		document.head.appendChild(link);
	}
	if (!document.getElementById('gjs-layout-fix')) {
		const s = document.createElement('style');
		s.id = 'gjs-layout-fix';
		s.textContent = [
			'.gjs-editor{--gjs-left-width:0px;--gjs-canvas-top:0px;--gjs-right-width:0px}.gjs-frame-wrapper__top{display:none!important}.gjs-off-prv{display:none!important}',
			// The canvas scrolls (wheel/keyboard) but hides its scrollbar entirely: a zero-width bar
			// reserves no space, so it can never toggle on/off and shift the page sideways.
			'.gjs-cv-canvas{overflow:auto!important;scrollbar-width:none!important;background:var(--muted)!important;padding:16px!important;box-sizing:border-box!important;display:flex!important;justify-content:center!important;align-items:flex-start!important}',
			'.gjs-cv-canvas::-webkit-scrollbar{width:0!important;height:0!important;display:none!important}',
			'.gjs-block{min-height:44px!important;height:auto!important;font-size:12px!important;padding:8px!important;border-radius:6px!important;line-height:1.25!important;display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important}',
			'.gjs-block-label,.gjs-block .gjs-block-label{font-size:12px!important;line-height:1.3!important}',
			// Frame fills the canvas VIEWPORT height (not a fixed 1219px floor). A fixed floor taller
			// than the visible canvas made the iframe overflow .gjs-cv-canvas, so the canvas gained
			// its own (scrollbar-hidden) scroll layer ON TOP of the iframe's internal scroll — two
			// nested scrollers, so scrolling one to its top still left a strip offscreen in the other.
			// height:100% keeps a single scroller: the iframe's own <html> scrolls the document content.
			'.gjs-cv-canvas__frames{height:100%!important;transform:none!important;transform-origin:top center!important}',
			'.gjs-frame-wrapper{height:100%!important}',
			'.gjs-frame{height:100%!important;background:var(--muted)!important}',
			// Hide the orange margin bars of the hover offset overlay, keeping the navy padding bars
			// (the content-area inset) visible. The margin band is noise on every hovered component
			// (the page and its content); the padding band is the useful one. GrapesJS colours margin
			// bars `#ff0` (`.gjs-margin-v-el` / `.gjs-fixedmargin-v-el`) and padding `navy`
			// (`.gjs-padding-v-el`, left visible).
			'.gjs-margin-v-el,.gjs-fixedmargin-v-el{display:none!important}'
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-blocks-compact')) {
		// Light-themed block panel (the default GrapesJS block manager is dark). Scoped by
		// the #gjs-blocks-panel id on the blocks container. Ported from the reference.
		const P = '#gjs-blocks-panel';
		const s = document.createElement('style');
		s.id = 'gjs-blocks-compact';
		s.textContent = [
			`${P}, ${P} .gjs-blocks-cs, ${P} .gjs-block-categories, ${P} .gjs-blocks-c, ${P} .gjs-blocks-no-cat{background:transparent!important;color:var(--foreground)!important;}`,
			`${P} .gjs-block-category{background:transparent!important;border:none!important;}`,
			`${P} .gjs-title{font-size:9px!important;padding:10px 10px 4px!important;font-weight:700!important;letter-spacing:.08em!important;text-transform:uppercase!important;color:var(--muted-foreground)!important;background:transparent!important;cursor:pointer!important;user-select:none!important;border:none!important;border-top:1px solid var(--border)!important;display:flex!important;align-items:center!important;gap:6px!important;}`,
			`${P} .gjs-block-category:first-child .gjs-title{border-top:none!important;}`,
			`${P} .gjs-block-category:not(.gjs-open) .gjs-title{opacity:.55!important;}`,
			`${P} .gjs-caret-icon{display:none!important;}`,
			`${P} .gjs-block-category.gjs-open .gjs-blocks-c{display:grid!important;grid-template-columns:1fr 1fr!important;gap:1px!important;padding:1px 4px 4px!important;}`,
			`${P} .gjs-block{padding:8px 6px!important;min-height:0!important;height:auto!important;width:auto!important;box-sizing:border-box!important;display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;gap:5px!important;border-radius:6px!important;border:1px solid var(--border)!important;background:var(--muted)!important;color:var(--foreground)!important;cursor:grab!important;transition:background .1s,border-color .1s,color .1s!important;}`,
			`${P} .gjs-block.gjs-one-bg{background:var(--muted)!important;color:var(--foreground)!important;}`,
			`${P} .gjs-block:hover, ${P} .gjs-block.gjs-four-color-h:hover{background:var(--accent)!important;border-color:var(--border)!important;box-shadow:none!important;color:var(--foreground)!important;}`,
			`${P} .gjs-block__media{display:flex!important;align-items:center!important;justify-content:center!important;height:20px!important;overflow:hidden!important;flex-shrink:0!important;color:var(--muted-foreground)!important;}`,
			`${P} .gjs-block__media svg{width:28px!important;height:18px!important;display:block!important;}`,
			`${P} .gjs-block-label{font-size:10px!important;font-weight:500!important;line-height:1.2!important;text-align:center!important;color:inherit!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;max-width:100%!important;}`
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-rte-light')) {
		const s = document.createElement('style');
		s.id = 'gjs-rte-light';
		s.textContent = [
			'.gjs-rte-toolbar{background:var(--popover)!important;border:none!important;border-radius:8px!important;box-shadow:0 2px 12px rgba(0,0,0,0.13),0 1px 3px rgba(0,0,0,0.08)!important;padding:2px!important;overflow:visible!important;}',
			'.gjs-rte-actionbar{display:flex!important;align-items:center!important;gap:0!important;padding:0!important;}',
			// width:auto overrides GrapesJS's fixed 25px so the font/size selects size to their content
			// (min-width keeps the single-letter buttons square-ish).
			'.gjs-rte-action{width:auto!important;min-width:28px!important;background:transparent!important;color:var(--foreground)!important;border:none!important;border-radius:5px!important;padding:0 6px!important;font-size:13px!important;font-weight:700!important;cursor:pointer!important;transition:background 0.1s!important;height:28px!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;line-height:1!important;box-sizing:border-box!important;flex:0 0 auto!important;}',
			'.gjs-rte-action:hover{background:var(--accent)!important;color:var(--foreground)!important;}',
			'.gjs-rte-action.gjs-rte-active{background:var(--brand-subtle)!important;color:var(--brand-strong)!important;}',
			// Font / size dropdowns embedded as toolbar actions
			'.gjs-rte-action .gjs-rte-select{-webkit-appearance:none!important;appearance:none!important;background:transparent!important;border:none!important;color:var(--foreground)!important;font-size:12px!important;font-weight:500!important;height:28px!important;padding:0 14px 0 4px!important;margin:0!important;cursor:pointer!important;outline:none!important;max-width:96px!important;background-image:url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%278%27 height=%278%27 viewBox=%270 0 8 8%27%3E%3Cpath d=%27M1 2.5L4 5.5L7 2.5%27 stroke=%27%236b7280%27 stroke-width=%271.2%27 fill=%27none%27 stroke-linecap=%27round%27/%3E%3C/svg%3E")!important;background-repeat:no-repeat!important;background-position:right 3px center!important;}',
			'.gjs-rte-action .gjs-rte-select:hover{color:var(--foreground)!important;}',
			// Font-size combobox — type a value or pick from the datalist dropdown.
			'.gjs-rte-action .gjs-rte-num{width:44px!important;background:transparent!important;border:none!important;color:var(--foreground)!important;font-size:12px!important;font-weight:500!important;height:28px!important;padding:0 2px 0 6px!important;margin:0!important;outline:none!important;-moz-appearance:textfield!important;}',
			'.gjs-rte-action .gjs-rte-num::-webkit-outer-spin-button,.gjs-rte-action .gjs-rte-num::-webkit-inner-spin-button{-webkit-appearance:none!important;margin:0!important;}',
			'.gjs-rte-action .gjs-rte-num::placeholder{color:var(--muted-foreground)!important;font-weight:400!important;}',
			// Font-size preset dropdown (custom, so the full list always shows)
			'.gjs-rte-action .gjs-rte-size-wrap{position:relative!important;display:inline-flex!important;align-items:center!important;}',
			'.gjs-rte-action .gjs-rte-size-caret{display:inline-flex!important;align-items:center!important;justify-content:center!important;width:14px!important;height:28px!important;cursor:pointer!important;opacity:.75!important;}',
			'.gjs-rte-action .gjs-rte-size-caret:hover{opacity:1!important;}',
			'.gjs-rte-action .gjs-rte-size-menu{position:absolute!important;top:calc(100% + 5px)!important;left:0!important;z-index:20!important;background:var(--popover)!important;border:1px solid var(--border)!important;border-radius:8px!important;box-shadow:0 6px 20px rgba(0,0,0,.14)!important;padding:4px!important;max-height:240px!important;overflow-y:auto!important;min-width:58px!important;display:flex!important;flex-direction:column!important;}',
			'.gjs-rte-action .gjs-rte-size-menu[hidden]{display:none!important;}',
			'.gjs-rte-action .gjs-rte-size-opt{padding:5px 10px!important;font-size:12px!important;font-weight:500!important;color:var(--foreground)!important;cursor:pointer!important;border-radius:5px!important;text-align:left!important;}',
			'.gjs-rte-action .gjs-rte-size-opt:hover{background:var(--accent)!important;color:var(--foreground)!important;}',
			// Text-colour swatch
			'.gjs-rte-action .gjs-rte-color{width:20px!important;height:20px!important;padding:0!important;border:1px solid var(--border)!important;border-radius:4px!important;background:transparent!important;cursor:pointer!important;}',
			'.gjs-rte-action .gjs-rte-color::-webkit-color-swatch-wrapper{padding:2px!important;}',
			'.gjs-rte-action .gjs-rte-color::-webkit-color-swatch{border:none!important;border-radius:2px!important;}',
			// The colour/select/number actions need no hover fill (the control carries its own affordance)
			'.gjs-rte-action:has(.gjs-rte-select):hover,.gjs-rte-action:has(.gjs-rte-color):hover,.gjs-rte-action:has(.gjs-rte-num):hover{background:transparent!important;}'
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-panels-theme-4')) {
		// Light-theme the Layers / Trait / Style managers (GrapesJS defaults are dark).
		// Layers rules ported from the reference (#gjs-layers-panel); traits/styles are
		// our own light overrides (the reference built bespoke React panels there).
		//
		// PARTLY DEAD, and deliberately not pruned yet. `styleManager: { custom: true }` means
		// GrapesJS no longer renders the property rail, so every `.gjs-sm-*` rule below now matches
		// nothing — around 80 of these touch `${Y}`. They are not simply deletable: `${both}` also
		// covers the TRAITS panel, which GrapesJS still renders, and `.gjs-field` is shared by traits
		// and the SelectorManager (which still mounts here). Telling those apart wants the editor
		// open in front of you, so the prune is a separate change with a visual check rather than a
		// guess folded into this one. Harmless meanwhile — a rule that matches nothing costs nothing
		// but the reading of it.
		const L = '#gjs-layers-panel';
		const T = '#gjs-traits-panel';
		const Y = '#gjs-styles-panel';
		const both = `${T}, ${Y}`;
		// Lucide glyphs as CSS masks so they inherit the button's currentColor.
		const imgIconSvg =
			"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='3' y='3' width='18' height='18' rx='2'/><circle cx='9' cy='9' r='2'/><path d='m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21'/></svg>";
		const imgIconMask = `url("data:image/svg+xml,${encodeURIComponent(imgIconSvg)}") center / contain no-repeat`;
		const uploadIconSvg =
			"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='17 8 12 3 7 8'/><line x1='12' y1='3' x2='12' y2='15'/></svg>";
		const uploadIconMask = `url("data:image/svg+xml,${encodeURIComponent(uploadIconSvg)}") center / contain no-repeat`;
		const s = document.createElement('style');
		s.id = 'gjs-panels-theme-4';
		s.textContent = [
			// Layers — shadcn colour scheme, square look, grab-on-hover.
			// No panel padding: the row background is full-bleed (see .gjs-layer-title-c gutter).
			`${L}{font-size:12px!important;}`,
			`${L} .gjs-layers{background:transparent!important;}`,
			// width:100% + border-box so the hover/selected highlight spans the full panel
			// width (reaches the right edge) instead of only its content.
			`${L} .gjs-layer-item{background:transparent!important;border:none!important;border-radius:0!important;min-height:28px!important;justify-content:flex-start!important;padding-left:0!important;width:100%!important;box-sizing:border-box!important;}`,
			// The whole row is the drag handle (see layerManager.extend), so the grip column is
			// gone entirely — hide the right-hand cluster (grip + child count) outright.
			`${L} .gjs-layer-item-right{display:none!important;}`,
			`${L} .gjs-layer-item-left{flex:1!important;width:auto!important;min-width:0!important;}`,
			`${L} .gjs-layer.gjs-hovered .gjs-layer-item{background:var(--accent)!important;}`,
			`${L} .gjs-layer.gjs-selected .gjs-layer-item{background:color-mix(in oklch,var(--brand) 12%,var(--background))!important;}`,
			// Keep GrapesJS's native per-level indent — inline padding-left on .gjs-layer-title,
			// which sits INSIDE the row — so the hover/selected highlight stays full-bleed (the
			// row background reaches both panel edges) while content still steps in per level.
			// A base gutter on the content container keeps top-level rows off the left edge
			// without insetting the highlight.
			`${L} .gjs-layer-title-c{padding-left:8px!important;}`,
			// Caret
			`${L} .gjs-layer-caret{color:var(--muted-foreground)!important;}`,
			// Layer name
			`${L} .gjs-layer-name{font-size:12px!important;font-weight:500!important;color:var(--foreground)!important;}`,
			`${L} .gjs-layer.gjs-selected .gjs-layer-name{color:color-mix(in oklch,var(--brand) 80%,var(--foreground))!important;}`,
			// Component icon
			`${L} .gjs-layer__icon{color:var(--muted-foreground)!important;opacity:.7!important;}`,
			// Eye, child count and grab handle — all hidden (the whole row is the drag handle now)
			`${L} .gjs-layer-vis{display:none!important;}`,
			`${L} .gjs-layer-count{display:none!important;}`,
			`${L} .gjs-layer-move{display:none!important;}`,
			// Signal draggability — grab cursor on the row, grabbing while dragging.
			`${L} .gjs-layer-item{cursor:grab!important;}`,
			`${L} .gjs-layer-item:active{cursor:grabbing!important;}`,
			// Hidden layers
			`${L} .gjs-layer-hidden .gjs-layer-name{opacity:.45!important;font-style:italic!important;}`,
			// Body (wrapper) row: showWrapper:false sometimes renders it and marks it `gjs-hidden`
			// (display:none) rather than promoting its children out — which collapses the Page/Text
			// rows nested inside it, emptying the panel (seen on freshly-created templates). Keep the
			// wrapper's layer box in flow and hide only its OWN row, so its children stay visible.
			// When GrapesJS instead promotes the children to the top level, no t-wrapper row exists
			// and these rules are inert.
			`${L} .gjs-layer.gjs-layer__t-wrapper{display:block!important;}`,
			`${L} .gjs-layer.gjs-layer__t-wrapper>.gjs-layer-item{display:none!important;}`,
			// Flip the GrapesJS CSS-variable theme inside the panels so every
			// descendant that uses var(--gjs-*) gets a light value automatically.
			`${both}{--gjs-primary-color:transparent;--gjs-main-color:transparent;--gjs-secondary-color:var(--border);--gjs-tertiary-color:var(--brand);--gjs-quaternary-color:var(--foreground);--gjs-font-color:var(--foreground);--gjs-font-color-active:var(--foreground);}`,
			// Traits + Styles shared: kill the dark backgrounds, set readable text
			`${both}{background:transparent!important;color:var(--foreground)!important;}`,
			`${both} .gjs-one-bg,${both} .gjs-bg-main{background-color:transparent!important;}`,
			`${both} .gjs-two-color,${both} .gjs-four-color,${both} .gjs-color-main,${both} .gjs-color-warn{color:var(--foreground)!important;background-color:transparent!important;}`,
			`${both} .gjs-label,${both} .gjs-sm-label,${both} .gjs-trt-trait__label,${both} .gjs-sm-sector-label{color:var(--foreground)!important;font-size:11px!important;font-weight:600!important;}`,
			`${both} .gjs-field,${both} .gjs-sm-field,${both} .gjs-clm-select,${both} .gjs-input-holder{background:var(--background)!important;border:1px solid var(--input)!important;border-radius:6px!important;color:var(--foreground)!important;}`,
			`${both} .gjs-field input,${both} .gjs-field select,${both} .gjs-field textarea,${both} .gjs-input-holder input{background:transparent!important;color:var(--foreground)!important;}`,
			`${both} .gjs-field input::placeholder{color:var(--muted-foreground)!important;opacity:1!important;}`,
			`${both} .gjs-field-units,${both} .gjs-input-unit,${both} .gjs-field-units *,${both} .gjs-field-units select{color:var(--muted-foreground)!important;}`,
			// Selects (font-family, display, position, etc.) blend into white otherwise — force dark text.
			`${both} select,${both} .gjs-field select,${both} .gjs-clm-select select,${both} .gjs-sm-field select,${both} .gjs-field-select{color:var(--foreground)!important;}`,
			// Native dropdown option list: force light theme so text isn't dark-on-dark.
			`${both} select option,${both} .gjs-field select option,${both} .gjs-sm-field select option{background-color:var(--popover)!important;color:var(--popover-foreground)!important;}`,
			`${both} .gjs-field-arrow-u{border-bottom-color:var(--muted-foreground)!important;}`,
			`${both} .gjs-field-arrow-d{border-top-color:var(--muted-foreground)!important;}`,
			// Green focus ring on fields — matches the primary accent used elsewhere.
			`${both} .gjs-field:focus-within,${both} .gjs-sm-field:focus-within{border-color:var(--brand)!important;box-shadow:0 0 0 1px color-mix(in oklch,var(--brand) 20%,transparent)!important;}`,
			// Checkboxes: green accent when checked.
			`${both} .gjs-field-checkbox input:checked+.gjs-chk-icon{border-color:var(--brand)!important;}`,
			// Trait rows — hide the container entirely when it has no children (avoids phantom empty box)
			`${T} .gjs-trt-traits:not(:has(.gjs-trt-trait)){display:none!important;}`,
			// Hide the whole traits panel when it has no traits (e.g. chart / table components)
			// — otherwise the empty panel renders as a thin brand-bordered bar under the section.
			`${T}:not(:has(.gjs-trt-trait)){display:none!important;}`,
			`${T} .gjs-trt-header{display:none!important;}`,
			`${T} .gjs-trt-traits{padding:8px!important;background:transparent!important;}`,
			`${T} .gjs-trt-trait{margin-bottom:5px!important;background:transparent!important;}`,
			`${T} .gjs-sel-arrow{border-top-color:var(--muted-foreground)!important;}`,
			// Style-manager sectors
			`${Y} .gjs-sm-sector,${Y} .gjs-sm-sectors{background:transparent!important;border-color:var(--border)!important;}`,
			`${Y} .gjs-sm-sector-title,${Y} .gjs-sm-title{background:transparent!important;color:var(--muted-foreground)!important;border-color:var(--border)!important;font-size:11px!important;font-weight:700!important;text-transform:uppercase!important;letter-spacing:.05em!important;padding:7px 8px!important;}`,
			`${Y} .gjs-sm-properties{padding:8px 8px 4px!important;}`,
			// Tighten per-property spacing so sectors read compactly.
			`${Y} .gjs-sm-property{margin-bottom:6px!important;padding:0!important;}`,
			`${Y} .gjs-sm-property .gjs-sm-label,${Y} .gjs-sm-property .gjs-label{margin-bottom:2px!important;}`,
			// Hide the entire selector/class manager and the per-property clear (×) button.
			`${Y} .gjs-clm-tags,${Y} .gjs-sm-header,${Y} .gjs-clm-header,${Y} .gjs-sm-clear{display:none!important;}`,
			// Active (open) sector — green dot after the title so it doesn't shift the text on open.
			`${Y} .gjs-sm-sector-title,${Y} .gjs-sm-title{display:flex!important;align-items:center!important;}`,
			`${Y} .gjs-sm-sector.gjs-sm-open>.gjs-sm-sector-title::after,${Y} .gjs-sm-sector.gjs-open>.gjs-sm-title::after,${Y} .gjs-sm-sector.gjs-sm-open>.gjs-sm-title::after{content:''!important;display:inline-block!important;width:6px!important;height:6px!important;border-radius:50%!important;background:var(--brand)!important;margin-left:auto!important;flex-shrink:0!important;}`,
			// Radio/list properties (font-weight, style, decoration, text-align) → segmented button group
			`${Y} .gjs-field-radio,${Y} .gjs-sm-field.gjs-sm-list{display:inline-flex!important;width:fit-content!important;gap:0!important;flex-wrap:nowrap!important;border:none!important;background:var(--muted)!important;border-radius:8px!important;padding:2px!important;box-shadow:none!important;}`,
			`${Y} .gjs-radio-item{flex:1 1 auto!important;border:none!important;border-radius:6px!important;overflow:hidden!important;background:transparent!important;}`,
			`${Y} .gjs-radio-item input{display:none!important;}`,
			`${Y} .gjs-radio-item-label{display:flex!important;align-items:center!important;justify-content:center!important;padding:5px 6px!important;font-size:11px!important;color:var(--muted-foreground)!important;font-weight:500!important;cursor:pointer!important;margin:0!important;border-radius:6px!important;transition:color .1s!important;}`,
			`${Y} .gjs-radio-item-label:hover{color:var(--foreground)!important;}`,
			`${Y} .gjs-radio-item input:checked+.gjs-radio-item-label,${Y} .gjs-radio-item.gjs-sm-radio-item-active .gjs-radio-item-label{background:var(--brand)!important;color:var(--brand-foreground)!important;box-shadow:0 1px 2px rgba(0,0,0,.12)!important;}`,
			// List items (text-align icons)
			`${Y} .gjs-sm-list .gjs-sm-el{flex:1 1 auto!important;border:none!important;border-radius:6px!important;background:transparent!important;float:none!important;}`,
			`${Y} .gjs-sm-list .gjs-sm-el label{display:flex!important;align-items:center!important;justify-content:center!important;padding:5px 6px!important;cursor:pointer!important;border-radius:6px!important;color:var(--muted-foreground)!important;transition:color .1s!important;}`,
			`${Y} .gjs-sm-list .gjs-sm-el label:hover{color:var(--foreground)!important;background:transparent!important;}`,
			`${Y} .gjs-sm-field.gjs-sm-list .gjs-sm-radio:checked+label{background:var(--brand)!important;color:var(--brand-foreground)!important;box-shadow:0 1px 2px rgba(0,0,0,.12)!important;}`,
			// Suppress phantom empty-state hint (we render our own in Svelte)
			`${Y} .gjs-sm-sectors:empty{display:none!important;}`,
			// Set-value cue. GrapesJS flags a property whose value is actually set by adding
			// .gjs-four-color to its label; the generic .gjs-four-color rule above flattened it
			// to the same grey as an unset label, so "Display: flex" looked identical to an empty
			// default. Mute the default label and light the set one with the brand accent (higher
			// specificity + later source order beats the generic rule).
			`${Y} .gjs-sm-property .gjs-sm-label,${Y} .gjs-sm-property .gjs-label{color:var(--muted-foreground)!important;font-weight:600!important;}`,
			`${Y} .gjs-sm-property .gjs-sm-label.gjs-four-color,${Y} .gjs-sm-property .gjs-label.gjs-four-color{color:var(--brand)!important;font-weight:700!important;}`,
			// ── shadcn-style Style-manager fields ────────────────────────────────────────
			// GrapesJS's default number/select fields are cramped and dated (inner-bordered
			// holder, dark unit text, tiny ns-resize spinner triangles). Restyle them to match
			// the shadcn Input: a single subtle-bordered pill, 30px tall, md radius, a muted unit
			// label, and a focus ring. Uses the theme tokens so it re-themes automatically. These
			// come after the generic field rules above, so (equal specificity) source order wins.
			// :not(.gjs-sm-layers) — the stack's layer-list container also carries .gjs-field;
			// excluding it stops this rule clamping the whole list to a 30px pill (styled below).
			`${Y} .gjs-field:not(.gjs-sm-layers){background:var(--background)!important;border:1px solid var(--input)!important;border-radius:var(--radius-md)!important;box-shadow:0 1px 1px rgba(15,23,42,.03)!important;height:30px!important;box-sizing:border-box!important;transition:border-color .12s ease,box-shadow .12s ease!important;}`,
			// Neutralise the inner input-holder's own border/bg (line above borders it too) so only
			// the field wrapper shows an outline; let it flex-fill the field.
			`${Y} .gjs-field .gjs-input-holder{background:transparent!important;border:none!important;border-radius:0!important;flex:1 1 auto!important;min-width:0!important;height:100%!important;}`,
			`${Y} .gjs-field-integer,${Y} .gjs-field-select{display:flex!important;align-items:center!important;}`,
			`${Y} .gjs-field input,${Y} .gjs-field select{height:100%!important;font-size:12px!important;font-weight:500!important;color:var(--foreground)!important;padding:0 9px!important;}`,
			`${Y} .gjs-field input::placeholder{color:var(--muted-foreground)!important;opacity:1!important;font-weight:400!important;}`,
			// Numeric fields: hide the spinner triangles (values are typed; shadcn shows none),
			// keep room for the unit, and style the unit <select> as a muted right-aligned label
			// (still a real select, so px/%/em stay switchable).
			`${Y} .gjs-field-integer input{padding-right:30px!important;}`,
			`${Y} .gjs-field-arrows{display:none!important;}`,
			`${Y} .gjs-field-units{right:0!important;top:0!important;bottom:0!important;display:flex!important;align-items:center!important;padding-right:9px!important;}`,
			`${Y} .gjs-field-unit,${Y} .gjs-field-units select,${Y} .gjs-input-unit{position:static!important;font-size:10px!important;font-weight:600!important;color:var(--muted-foreground)!important;text-transform:lowercase!important;padding:0!important;width:auto!important;height:auto!important;}`,
			`${Y} .gjs-field-units select{cursor:pointer!important;background:transparent!important;-webkit-appearance:none!important;appearance:none!important;text-align:right!important;}`,
			`${Y} .gjs-field:not(.gjs-sm-layers):focus-within{border-color:var(--ring)!important;box-shadow:0 0 0 3px color-mix(in oklch,var(--ring) 35%,transparent)!important;}`,
			// ── shadcn-style stacks (box-shadow / transition / transform layers) ─────────
			// A stack renders an add button + a list of layer rows; each layer expands to its
			// sub-properties (the x/y/blur/spread/color fields, already styled above). Strip the
			// inherited borders and lay the layers out as clean bordered pill rows.
			`${Y} .gjs-sm-field.gjs-sm-stack{border:none!important;background:transparent!important;position:relative!important;}`,
			`${Y} .gjs-sm-layers{height:auto!important;min-height:0!important;border:none!important;border-radius:0!important;box-shadow:none!important;background:transparent!important;padding:0!important;display:flex!important;flex-direction:column!important;gap:4px!important;}`,
			`${Y} .gjs-sm-layer{background:transparent!important;padding:0!important;}`,
			`${Y} .gjs-sm-layer .gjs-sm-label-wrp{display:flex!important;align-items:center!important;gap:8px!important;height:30px!important;padding:0 8px!important;border:1px solid var(--input)!important;border-radius:var(--radius-md)!important;background:var(--background)!important;box-sizing:border-box!important;transition:border-color .12s,background .12s!important;}`,
			`${Y} .gjs-sm-layer .gjs-sm-label-wrp:hover{background:var(--accent)!important;}`,
			`${Y} .gjs-sm-layer.gjs-sm-active .gjs-sm-label-wrp{border-color:var(--ring)!important;box-shadow:0 0 0 3px color-mix(in oklch,var(--ring) 25%,transparent)!important;}`,
			`${Y} [data-move-layer]{display:flex!important;align-items:center!important;color:var(--muted-foreground)!important;cursor:grab!important;flex:0 0 auto!important;}`,
			`${Y} [data-move-layer]:active{cursor:grabbing!important;}`,
			`${Y} [data-move-layer] svg{width:13px!important;height:13px!important;}`,
			`${Y} .gjs-sm-layer [data-label]{flex:1 1 auto!important;min-width:0!important;font-size:12px!important;font-weight:500!important;color:var(--foreground)!important;font-variant-numeric:tabular-nums!important;overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important;}`,
			`${Y} .gjs-sm-layer-preview{flex:0 0 auto!important;width:18px!important;height:18px!important;border:1px solid var(--border)!important;border-radius:4px!important;background:var(--card)!important;overflow:hidden!important;position:relative!important;}`,
			`${Y} .gjs-sm-btn-close{display:flex!important;align-items:center!important;justify-content:center!important;color:var(--muted-foreground)!important;cursor:pointer!important;flex:0 0 auto!important;opacity:.65!important;transition:opacity .12s,color .12s!important;}`,
			`${Y} .gjs-sm-btn-close:hover{opacity:1!important;color:var(--foreground)!important;}`,
			`${Y} .gjs-sm-btn-close svg{width:13px!important;height:13px!important;}`,
			`${Y} #gjs-sm-add,${Y} [data-add-layer]{display:flex!important;align-items:center!important;justify-content:center!important;width:22px!important;height:22px!important;border:1px solid var(--input)!important;border-radius:var(--radius-sm)!important;background:var(--background)!important;color:var(--muted-foreground)!important;cursor:pointer!important;transition:background .12s,color .12s,border-color .12s!important;}`,
			`${Y} #gjs-sm-add:hover,${Y} [data-add-layer]:hover{background:var(--accent)!important;color:var(--foreground)!important;border-color:var(--ring)!important;}`,
			`${Y} #gjs-sm-add svg,${Y} [data-add-layer] svg{width:14px!important;height:14px!important;}`,
			`${Y} [data-properties]{padding:8px 2px 2px!important;}`,
			// Background image: two equal 50/50 square tiles — the asset-manager opener and an
			// always-visible image box (placeholder when empty, thumbnail when set). GrapesJS
			// stacks them vertically by default and hides the preview until an asset exists.
			// Scope to the inner .gjs-sm-field — the property container also carries .gjs-sm-file,
			// and a bare .gjs-sm-file would lay the whole property out as a row, pushing the
			// "Background image" label beside the tiles instead of above them. Grid (not flex)
			// gives exact 50/50 columns; aspect-ratio then makes each cell square.
			`${Y} .gjs-sm-field.gjs-sm-file{display:grid!important;grid-template-columns:1fr 1fr!important;gap:8px!important;border:none!important;background:transparent!important;box-shadow:none!important;padding:0!important;}`,
			`${Y} .gjs-sm-field.gjs-sm-file #gjs-sm-input-holder{min-width:0!important;aspect-ratio:1/1!important;border:none!important;background:transparent!important;}`,
			`${Y} .gjs-sm-field.gjs-sm-file .gjs-sm-btn-c{height:100%!important;}`,
			// Opener → fills its square tile: shadcn outline button, image icon above the label.
			// font-size:0 collapses GrapesJS's built-in "Images" text node; ::after supplies a
			// clearer label ("Upload image"). Done in CSS so it survives style-manager re-renders.
			`${Y} #gjs-sm-images,${Y} .gjs-sm-btn[data-open-assets]{display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;gap:0!important;width:100%!important;height:100%!important;padding:0!important;border:1px solid var(--input)!important;border-radius:var(--radius-md)!important;background:var(--background)!important;color:var(--foreground)!important;font-size:0!important;font-weight:500!important;cursor:pointer!important;box-shadow:0 1px 1px rgba(15,23,42,.03)!important;transition:background .12s,border-color .12s,color .12s!important;}`,
			`${Y} #gjs-sm-images:hover,${Y} .gjs-sm-btn[data-open-assets]:hover{background:var(--accent)!important;border-color:var(--ring)!important;}`,
			`${Y} #gjs-sm-images::before,${Y} .gjs-sm-btn[data-open-assets]::before{content:''!important;width:22px!important;height:22px!important;flex:0 0 auto!important;background-color:currentColor!important;-webkit-mask:${uploadIconMask}!important;mask:${uploadIconMask}!important;}`,
			`${Y} #gjs-sm-images::after,${Y} .gjs-sm-btn[data-open-assets]::after{content:'Upload image'!important;margin-top:8px!important;font-size:12px!important;font-weight:500!important;line-height:1.2!important;color:var(--foreground)!important;text-align:center!important;}`,
			// Image box: forced visible (GrapesJS hides it when empty) so it reads as a placeholder;
			// square, muted, with a faint image-icon watermark centred via ::before.
			`${Y} #gjs-sm-preview-box{display:flex!important;align-items:center!important;justify-content:center!important;position:relative!important;min-width:0!important;aspect-ratio:1/1!important;border:1px solid var(--input)!important;border-radius:var(--radius-md)!important;overflow:hidden!important;background:var(--muted)!important;box-sizing:border-box!important;margin:0!important;}`,
			`${Y} #gjs-sm-preview-box::before{content:''!important;width:28px!important;height:28px!important;background-color:var(--muted-foreground)!important;opacity:.35!important;-webkit-mask:${imgIconMask}!important;mask:${imgIconMask}!important;}`,
			// Selected thumbnail (cover) sits above the placeholder watermark.
			`${Y} #gjs-sm-preview-file{position:absolute!important;inset:0!important;width:100%!important;height:100%!important;background-size:cover!important;background-position:center!important;background-repeat:no-repeat!important;}`,
			// Remove (×) — appears on hover so the empty placeholder stays clean.
			`${Y} #gjs-sm-close{position:absolute!important;top:4px!important;right:4px!important;width:16px!important;height:16px!important;display:flex!important;align-items:center!important;justify-content:center!important;border-radius:9999px!important;background:color-mix(in oklch,var(--popover) 90%,transparent)!important;color:var(--foreground)!important;cursor:pointer!important;box-shadow:0 1px 2px rgba(0,0,0,.15)!important;opacity:0!important;transition:opacity .12s!important;}`,
			`${Y} #gjs-sm-preview-box:hover #gjs-sm-close{opacity:1!important;}`,
			// No image set: GrapesJS marks the preview box with inline display:none (we keep it
			// visible as the placeholder via !important, but match that signal to hide the × —
			// there's nothing to remove yet).
			`${Y} #gjs-sm-preview-box[style*="display: none"] #gjs-sm-close{display:none!important;}`,
			`${Y} #gjs-sm-close svg{width:10px!important;height:10px!important;}`
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-chrome-theme')) {
		// Canvas chrome — GrapesJS's selection highlighter, name badge and the component
		// action toolbar ship in its own default blue. Re-theme them to the platform
		// brand (teal) with a cleaner shadcn look. The toolbar (move / drag / copy / delete
		// that floats above the selected component) is re-enabled here after being hidden.
		const s = document.createElement('style');
		s.id = 'gjs-chrome-theme';
		s.textContent = [
			// Selection + hover outlines → brand.
			'.gjs-cv-canvas .gjs-highlighter,.gjs-cv-canvas .gjs-highlighter-sel{outline-color:var(--brand)!important;}',
			// Component name badge → brand pill.
			'.gjs-badge,.gjs-com-badge{background-color:var(--brand)!important;color:var(--brand-foreground)!important;border-radius:5px 5px 0 0!important;font-size:11px!important;font-weight:600!important;padding:2px 7px!important;letter-spacing:.01em!important;}',
			// Component toolbar → brand pill, white icons, subtle elevation.
			'.gjs-toolbar{display:block!important;background:var(--brand)!important;border-radius:6px!important;overflow:hidden!important;box-shadow:0 2px 8px rgba(15,23,42,.22)!important;}',
			'.gjs-toolbar-items{display:flex!important;align-items:center!important;}',
			'.gjs-toolbar-item{width:auto!important;min-width:26px!important;height:26px!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;padding:0 6px!important;color:var(--brand-foreground)!important;cursor:pointer!important;transition:background .1s!important;}',
			'.gjs-toolbar-item:hover{background:color-mix(in oklch,var(--brand-foreground) 18%,transparent)!important;}',
			'.gjs-toolbar-item svg{fill:currentColor!important;width:15px!important;height:15px!important;}'
		].join('');
		document.head.appendChild(s);
	}
	if (!document.getElementById('gjs-modal-theme')) {
		// Modals + Asset Manager ("Select Image") — GrapesJS ships them dark; re-theme to the
		// platform light/shadcn look so the image picker matches the rest of the editor.
		const s = document.createElement('style');
		s.id = 'gjs-modal-theme';
		s.textContent = [
			// Backdrop + dialog shell
			'.gjs-mdl-backdrop{background:color-mix(in oklch,var(--hero-base) 45%,transparent)!important;}',
			'.gjs-mdl-dialog{background:var(--background)!important;color:var(--foreground)!important;border:1px solid var(--border)!important;border-radius:12px!important;box-shadow:0 20px 50px rgba(15,23,42,.28)!important;overflow:hidden!important;}',
			'.gjs-mdl-header{background:transparent!important;border-bottom:1px solid var(--border)!important;padding:13px 16px!important;display:flex!important;align-items:center!important;}',
			'.gjs-mdl-title{color:var(--foreground)!important;font-size:14px!important;font-weight:600!important;}',
			'.gjs-mdl-btn-close{color:var(--muted-foreground)!important;font-size:22px!important;opacity:1!important;transition:color .12s!important;}',
			'.gjs-mdl-btn-close:hover{color:var(--foreground)!important;}',
			'.gjs-mdl-content{background:var(--background)!important;color:var(--foreground)!important;padding:16px!important;}',
			// Asset manager containers
			'.gjs-am-assets-cont,.gjs-am-assets-header{background:transparent!important;border:none!important;padding:0!important;}',
			'.gjs-am-assets-header{margin-bottom:12px!important;}',
			// Upload dropzone
			'.gjs-am-file-uploader{margin:0!important;}',
			'#gjs-am-uploadFile,.gjs-am-file-uploader>div{background:var(--muted)!important;border:2px dashed var(--input)!important;border-radius:var(--radius-md)!important;color:var(--muted-foreground)!important;font-size:13px!important;transition:border-color .12s,background .12s,color .12s!important;}',
			'#gjs-am-uploadFile:hover,.gjs-am-file-uploader>div:hover{border-color:var(--brand)!important;background:var(--accent)!important;color:var(--foreground)!important;}',
			// URL field + "Add image" button
			'.gjs-am-add-asset{display:flex!important;gap:8px!important;align-items:stretch!important;}',
			'.gjs-am-add-asset .gjs-am-add-field{flex:1 1 auto!important;background:var(--background)!important;border:1px solid var(--input)!important;border-radius:var(--radius-md)!important;padding:0!important;display:flex!important;align-items:center!important;}',
			'.gjs-am-add-asset .gjs-am-add-field input{background:transparent!important;border:none!important;color:var(--foreground)!important;padding:0 10px!important;height:36px!important;width:100%!important;font-size:13px!important;}',
			'.gjs-am-add-asset .gjs-am-add-field input::placeholder{color:var(--muted-foreground)!important;}',
			'.gjs-am-add-asset .gjs-btn-prim,.gjs-am-add-asset button{background:var(--brand)!important;color:var(--brand-foreground)!important;border:none!important;border-radius:var(--radius-md)!important;font-size:13px!important;font-weight:600!important;padding:0 16px!important;min-height:36px!important;white-space:nowrap!important;cursor:pointer!important;transition:filter .12s!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;text-align:center!important;}',
			'.gjs-am-add-asset .gjs-btn-prim:hover,.gjs-am-add-asset button:hover{filter:brightness(1.06)!important;}',
			// Asset grid + cards
			'.gjs-am-assets{background:transparent!important;}',
			'.gjs-am-asset{background:var(--card)!important;border:1px solid var(--border)!important;border-radius:var(--radius-md)!important;overflow:hidden!important;}',
			'.gjs-am-asset.gjs-am-highlight,.gjs-am-asset:hover{border-color:var(--brand)!important;}',
			'.gjs-am-meta,.gjs-am-name{color:var(--foreground)!important;}',
			'.gjs-am-dimensions{color:var(--muted-foreground)!important;}',
			'.gjs-am-close{color:var(--hero-foreground)!important;background:color-mix(in oklch,var(--hero-base) 55%,transparent)!important;border-radius:9999px!important;}'
		].join('');
		document.head.appendChild(s);
	}
}

/**
 * Tailor the rich-text toolbar: drop the actions we don't want (link, wrap) and
 * add text colour, font family and font size. The colour/font controls are native
 * <input>/<select> embedded as the action icon — GrapesJS fires the action on the
 * `event` we declare and exposes the element as `action.btn.firstChild`.
 * styleWithCSS is enabled on RTE entry so these produce inline `style=` spans rather
 * than deprecated <font> tags (cleaner serialised HTML).
 */
function configureRte(editor: any): void {
	const rte = editor.RichTextEditor;
	if (!rte) return;

	// Remove the built-in link + wrap-for-style actions.
	['link', 'wrap'].forEach((name) => {
		try {
			rte.remove(name);
		} catch {
			/* not registered */
		}
	});

	const FONTS: [string, string][] = [
		['', 'Font'],
		['Arial, Helvetica, sans-serif', 'Arial'],
		['Georgia, serif', 'Georgia'],
		[`'Times New Roman', Times, serif`, 'Times'],
		[`'Courier New', monospace`, 'Courier'],
		['Verdana, Geneva, sans-serif', 'Verdana'],
		['Tahoma, sans-serif', 'Tahoma'],
		[`'Trebuchet MS', sans-serif`, 'Trebuchet']
	];
	// Normalise a font-family string to its primary family, lower-cased & unquoted.
	const primaryFamily = (ff: string) =>
		(ff || '').split(',')[0]!.replace(/["']/g, '').trim().toLowerCase();

	const rgbToHex = (rgb: string): string => {
		const m = (rgb || '').match(/\d+/g);
		if (!m || m.length < 3) return '';
		return `#${m
			.slice(0, 3)
			.map((n) => Number(n).toString(16).padStart(2, '0'))
			.join('')}`;
	};

	// Resolve the GrapesJS component whose element owns the current selection anchor —
	// used to read the effective font/size for reflecting into the toolbar controls.
	const selectionEl = (rte: any): HTMLElement | null => {
		try {
			const sel = rte.doc.getSelection?.();
			let node: any = sel?.anchorNode;
			if (node?.nodeType === 3) node = node.parentElement;
			return node?.nodeType === 1 ? node : null;
		} catch {
			return null;
		}
	};

	// Each option is rendered in its own font so the dropdown previews the typeface.
	const fontOpts = FONTS.map(
		([v, l]) => `<option value="${v}"${v ? ` style="font-family:${v}"` : ''}>${l}</option>`
	).join('');
	rte.add('fontName', {
		icon: `<select class="gjs-rte-select gjs-rte-font" title="Font family">${fontOpts}</select>`,
		event: 'change',
		result: (rte: any, action: any) => {
			const select = action.btn.firstChild as HTMLSelectElement;
			const v = select.value;
			if (v) rte.exec('fontName', v);
			// Preview the chosen face on the closed control right away.
			select.style.fontFamily = v;
		},
		update: (rte: any, action: any) => {
			// Show the current family, or fall back to the "Font" placeholder when the
			// selection spans multiple fonts / an unlisted one.
			const select = action.btn.firstChild as HTMLSelectElement;
			let current = '';
			try {
				// queryCommandValue returns '' for a mixed-font selection.
				current = primaryFamily(rte.doc.queryCommandValue('fontName') as string);
			} catch {
				/* unsupported */
			}
			if (!current) {
				const el = selectionEl(rte);
				if (el) current = primaryFamily(rte.doc.defaultView.getComputedStyle(el).fontFamily);
			}
			const match = FONTS.find(([val]) => val && primaryFamily(val) === current);
			select.value = match ? match[0] : '';
			select.style.fontFamily = match ? match[0] : '';
		}
	});

	// Font size — a dropdown of standard point sizes. execCommand('fontSize') only takes
	// the legacy 1–7 scale, so we tag the selection with size 7 (styleWithCSS off → real
	// <font> tags), then rewrite those into <span style="font-size:Npx"> for the exact value.
	// Editable combobox: type any size in the number field, or pick a preset from the
	// custom dropdown. (A native <datalist> can't be used — the browser filters its
	// options down to whatever's been typed, so a manual value empties the list.)
	const SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72];
	// Inline (not a data: URI) so `currentColor` inherits the token-driven toolbar ink.
	const caret = `<svg width="8" height="8" viewBox="0 0 8 8" fill="none"><path d="M1 2.5L4 5.5L7 2.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>`;
	rte.add('fontSize', {
		icon:
			`<span class="gjs-rte-size-wrap">` +
			`<input type="number" min="1" max="400" step="1" class="gjs-rte-num" title="Font size (px)" placeholder="Size" />` +
			`<span class="gjs-rte-size-caret" title="Preset sizes">${caret}</span>` +
			`<span class="gjs-rte-size-menu" hidden>` +
			SIZES.map((n) => `<span class="gjs-rte-size-opt" data-v="${n}">${n}</span>`).join('') +
			`</span></span>`,
		event: 'change',
		result: (rte: any, action: any) => {
			const px = parseInt(action.btn.querySelector('.gjs-rte-num').value, 10);
			if (!px || px < 1) return;
			const doc = rte.doc;
			try {
				doc.execCommand('styleWithCSS', false, false);
				doc.execCommand('fontSize', false, '7');
				doc.execCommand('styleWithCSS', false, true);
				const spans: HTMLElement[] = [];
				Array.from(doc.querySelectorAll('font[size="7"]')).forEach((el: any) => {
					const span = doc.createElement('span');
					span.style.fontSize = `${px}px`;
					while (el.firstChild) span.appendChild(el.firstChild);
					el.replaceWith(span);
					spans.push(span);
				});
				// replaceWith detaches the original range; re-select *inside* the resized spans
				// (start/end within the span elements, not around them) so the caret's element is
				// the styled span — otherwise update reads the container and always shows 13.
				if (spans.length) {
					const sel = doc.getSelection?.();
					const range = doc.createRange();
					const last = spans[spans.length - 1];
					range.setStart(spans[0]!, 0);
					range.setEnd(last!, last!.childNodes.length);
					sel?.removeAllRanges();
					sel?.addRange(range);
				}
			} catch {
				/* exec unsupported */
			}
		},
		update: (rte: any, action: any) => {
			// Reflect the selection's current size back into the input.
			const input = action.btn.querySelector('.gjs-rte-num') as HTMLInputElement | null;
			if (!input) return;
			const el = selectionEl(rte);
			if (!el) return;
			const px = Math.round(parseFloat(rte.doc.defaultView.getComputedStyle(el).fontSize));
			input.value = px ? String(px) : '';
		}
	});

	rte.add('foreColor', {
		icon: `<input type="color" class="gjs-rte-color" title="Text colour" />`,
		event: 'input',
		result: (rte: any, action: any) => rte.exec('foreColor', action.btn.firstChild.value),
		update: (rte: any, action: any) => {
			// Preview the selection's current colour in the swatch.
			const el = selectionEl(rte);
			if (!el) return;
			try {
				const hex = rgbToHex(rte.doc.defaultView.getComputedStyle(el).color);
				if (hex) action.btn.firstChild.value = hex;
			} catch {
				/* unsupported */
			}
		}
	});

	// Emit CSS styles (spans) rather than <font> tags.
	editor.on('rte:enable', () => {
		try {
			editor.Canvas?.getDocument?.()?.execCommand('styleWithCSS', false, true);
		} catch {
			/* ignore */
		}
	});

	// Custom font-size dropdown behaviour. Delegated + capture-phase because GrapesJS's RTE
	// toolbar stops mousedown propagation (to preserve the text selection), so a bubble-phase
	// listener never sees the click. mousedown + preventDefault also keeps the selection intact.
	// Wired per-document (parent + canvas iframe) since the toolbar's host frame can vary.
	const sizeMenuHandler = (e: any) => {
		const doc: Document = e.target?.ownerDocument ?? document;
		const closeMenus = (except?: Element | null) =>
			doc.querySelectorAll('.gjs-rte-size-menu').forEach((m) => {
				if (m !== except) (m as HTMLElement).hidden = true;
			});
		const caretEl = e.target?.closest?.('.gjs-rte-size-caret');
		if (caretEl) {
			e.preventDefault();
			const menu = caretEl.parentElement?.querySelector('.gjs-rte-size-menu') as HTMLElement | null;
			if (menu) {
				const show = menu.hidden;
				closeMenus(menu);
				menu.hidden = !show;
			}
			return;
		}
		const opt = e.target?.closest?.('.gjs-rte-size-opt');
		if (opt) {
			e.preventDefault();
			const input = opt
				.closest('.gjs-rte-size-wrap')
				?.querySelector('.gjs-rte-num') as HTMLInputElement | null;
			if (input) {
				input.value = opt.getAttribute('data-v') || '';
				const win = input.ownerDocument.defaultView ?? window;
				input.dispatchEvent(new win.Event('change', { bubbles: true }));
			}
			closeMenus();
			return;
		}
		if (!e.target?.closest?.('.gjs-rte-size-wrap')) closeMenus();
	};
	const wireSizeMenu = (doc: any) => {
		if (!doc || doc.__gjsRteSizeMenu) return;
		doc.__gjsRteSizeMenu = true;
		doc.addEventListener('mousedown', sizeMenuHandler, true);
	};
	wireSizeMenu(document);
	editor.on('load', () => wireSizeMenu(editor.Canvas?.getDocument?.()));
	editor.on('rte:enable', () => wireSizeMenu(editor.Canvas?.getDocument?.()));
}

/**
 * CJS↔ESM interop for the two GrapesJS packages.
 *
 * Both are UMD bundles whose `module.exports` is `{ __esModule: true, default: … }`. Whether
 * `import()` hands back that wrapper or the value inside it is the bundler's decision, and it is
 * not stable between the dev server and a build, or across a dependency-optimiser rebuild — which
 * is why this failed on the document editor while the email editor, which resolves the shape
 * explicitly, kept working. A bare `{ default: … }` destructure is a bet on one of them.
 *
 * The two packages want OPPOSITE things, and conflating them is its own bug: `grapesjs` is an
 * OBJECT (identified by `.init`) and the plugin is a FUNCTION. A single "unwrap until callable"
 * helper walks straight past the grapesjs namespace and fails with "grapesjs did not export a
 * function", which is how this file briefly traded one broken editor for another.
 *
 * (`pkgs/ui/src/lib/components/email-editor/create.ts` resolves the same two shapes for the email
 * editor. Deliberately NOT shared: that editor works, and a refactor across it to remove the
 * duplication would put a working surface at risk to tidy up eleven lines.)
 */
function resolveGrapesJs(mod: unknown): any {
	const m = mod as any;
	return typeof m?.default?.init === 'function' ? m.default : m;
}

/** A GrapesJS plugin — a function, however deeply the interop wrapped it. */
function resolveGrapesPlugin(mod: unknown, name: string): any {
	const m = mod as any;
	const fn = [m?.default, m?.default?.default, m].find((c) => typeof c === 'function');
	if (typeof fn !== 'function') {
		// Named, because this surfaces in the editor's own error panel: "grapesjs-blocks-basic did
		// not resolve to a plugin function" is a fixable sentence where "blocksBasic is not a
		// function" is a stack trace the reader sees as a blank canvas.
		throw new Error(`${name} did not resolve to a plugin function`);
	}
	return fn;
}

export async function createEditor(opts: CreateEditorOpts): Promise<any> {
	ensureEditorStyles();

	const [grapesjsMod, blocksBasicMod] = await Promise.all([
		import('grapesjs'),
		import('grapesjs-blocks-basic')
	]);
	const grapesjs = resolveGrapesJs(grapesjsMod);
	const blocksBasic = resolveGrapesPlugin(blocksBasicMod, 'grapesjs-blocks-basic');

	const editor = grapesjs.init({
		container: opts.container,
		height: '100%',
		width: 'auto',
		storageManager: false, // manual save only
		panels: { defaults: [] },
		blockManager: { appendTo: opts.blocksEl },
		// showWrapper:false hides the "Body" (wrapper) root row so the doc-pages sit at the
		// top level of the layer tree rather than nested one level under Body.
		// extend adds a whole-row drag handle: the grab bar is hidden (see CSS), so instead of
		// dragging only from the grip we start the sorter on a mousedown anywhere on the row
		// (except the caret / name zones, which own the expand + rename gestures). A plain click
		// never reorders — the sorter only moves once the pointer actually moves.
		layerManager: {
			appendTo: opts.layersEl,
			showWrapper: false,
			extend: {
				events(this: any) {
					return {
						'mousedown [data-toggle-move]': 'startSort',
						'touchstart [data-toggle-move]': 'startSort',
						'mousedown [data-toggle-select]': 'startSortFromRow',
						'touchstart [data-toggle-select]': 'startSortFromRow',
						'click [data-toggle-visible]': 'toggleVisibility',
						// Whole row toggles expand/collapse (and selects) — the caret's own binding is
						// dropped so a click on it isn't handled twice. See handleRowClick.
						'click [data-toggle-select]': 'handleRowClick',
						'mouseover [data-toggle-select]': 'handleHover',
						'mouseout [data-toggle-select]': 'handleHoverOut',
						'dblclick [data-name]': 'handleEdit',
						'keydown [data-name]': 'handleEditKey',
						'focusout [data-name]': 'handleEditEnd'
					};
				},
				startSortFromRow(this: any, ev: any) {
					// Mirror the grip's own gate — only draggable components can be reordered.
					if (!this.model?.get('draggable')) return;
					// Leave the caret (expand/collapse) and name (rename) hit-areas alone.
					const t = ev.target as HTMLElement | null;
					if (t?.closest?.('[data-toggle-open]') || t?.closest?.('[data-name]')) return;
					this.startSort(ev);
				},
				handleRowClick(this: any, ev: any) {
					// Clicking anywhere on the row selects it and expands/collapses its children
					// (toggleOpening no-ops for leaf rows). Replaces the caret-only toggle.
					this.handleSelect(ev);
					this.toggleOpening(ev);
				}
			}
		},
		traitManager: { appendTo: opts.traitsEl },
		selectorManager: { appendTo: opts.stylesEl, label: '', statesLabel: '', selectedLabel: '' },
		// `custom: true` suppresses GrapesJS's own style VIEW and nothing else: the sectors below are
		// still built, every Property model still resolves against the selection, and the rail is
		// rendered by `@graphene/ui/grapes`'s StylePanel instead. GrapesJS's default UI is precisely
		// the native widget docs/design-language.md exists to keep out — and it had its own dark
		// stylesheet fighting the tenant's theme.
		//
		// `selectorManager` deliberately KEEPS its `appendTo` for now: it renders the class/state
		// control, which is a separate surface from the property rail, and blanking it is a decision
		// about how a document template is targeted rather than about how it is styled.
		styleManager: { custom: true, sectors: STYLE_SECTORS },
		// Spectrum colour-picker options (merged into every SM/RTE colour field).
		// showInput  → a typeable hex/name field so colours can be entered by keyboard.
		// showButtons + OK/Cancel → commit vs revert is explicit; Escape (which reverts)
		//   now reads as an obvious Cancel instead of a silent discard.
		// clickoutFiresChange → clicking the canvas/outside commits (matches prior behaviour).
		// Viewport-collision handling is added separately (clampColorPicker) since Spectrum
		// anchors the popover directly below the swatch with no flip.
		colorPicker: {
			showInput: true,
			showButtons: true,
			chooseText: 'OK',
			cancelText: 'Cancel',
			clickoutFiresChange: true,
			preferredFormat: 'hex'
		},
		canvas: {
			styles: ['/grapesjs-canvas.css'],
			scripts: [
				'/grapesjs-placeholder.js',
				'/grapesjs-jinja-vars.js',
				'/grapesjs-box-model.js',
				'/grapesjs-page-controls.js'
			]
		},
		plugins: [
			(e: any) => blocksBasic(e, { flexGrid: 1, blocks: ['text', 'image'], category: 'Basic' })
		]
	} as any);

	registerComponentTypes(editor, opts.getSchema, opts.getDatasetSample);
	registerBlocks(editor, opts.getSchema, opts.brandLogo ?? null);
	configureRte(editor);

	// Keep every Spectrum colour popover inside the viewport (see watchColorPickers).
	const stopColorPickerWatch = watchColorPickers();
	editor.on('destroy', stopColorPickerWatch);

	// The body wrapper is the page container, not an editable element — don't let it be
	// selected/hovered (clicks should land on the page content).
	editor
		.getWrapper()
		?.set({ selectable: false, hoverable: false, highlightable: false, layerable: false });

	// Give every freshly-dropped chart/table a stable id. avoidStore keeps this derived write OUT of
	// the undo stack, so dropping a chart is a single "Add element" step rather than add + attribute.
	const assignChartId = (comp: any) => {
		const attrs = comp.get?.('attributes') ?? {};
		if (attrs['data-comp-id'] === 'comp_NEW') {
			comp.addAttributes?.(
				{ 'data-comp-id': `comp_${Math.random().toString(36).slice(2, 10)}` },
				{ avoidStore: true }
			);
		}
	};
	editor.on('component:add', assignChartId);
	editor.on('component:mount', assignChartId);

	loadInto(editor, opts.initial);

	editor.on('load', () => {
		// Isolate each restored page's styling class (templates built before per-page classes shared
		// `.page`, so a background edit leaked onto every page).
		ensurePageClasses(editor);

		// Set a helpful placeholder on the selector-manager input once GrapesJS has rendered it.
		const input = document.querySelector<HTMLInputElement>('#gjs-clm-new');
		if (input) input.placeholder = 'Add or search class…';

		// Override the grapesjs-blocks-basic Text block to include data-tmpl-ph so that
		// grapesjs-placeholder.js can select-all on first edit (replacing "Insert your text
		// here" cleanly). Runs inside 'load' so this doesn't affect components being restored
		// from localStorage — only future drags from the palette pick up the new content.
		const textBlock = editor.BlockManager?.get('text');
		if (textBlock) {
			textBlock.set('content', {
				type: 'text',
				content: 'Insert your text here',
				style: { padding: '10px' },
				attributes: { 'data-tmpl-ph': '' }
			});
		}
	});

	// grapesjs-placeholder.js (running in the canvas iframe) sends this message when the user
	// actually types in a [data-tmpl-ph] element for the first time. We use it to gate the
	// model cleanup: only strip data-tmpl-ph from the model when the user has really edited,
	// not when they just entered and left without typing (so the next entry still selects-all).
	let pendingPhCleanup = false;
	window.addEventListener('message', (e: MessageEvent) => {
		if (e.data?.type === 'gjs-tmpl-ph-removed') pendingPhCleanup = true;
	});

	// On RTE exit, if the user typed during that session, remove data-tmpl-ph from the
	// GrapesJS model so it isn't serialised to localStorage and doesn't re-appear on reload.
	editor.on('rte:disable', (view: any) => {
		if (!pendingPhCleanup) return;
		pendingPhCleanup = false;
		const model = view?.model;
		if (!model) return;
		const attrs: Record<string, unknown> = { ...(model.get?.('attributes') ?? {}) };
		if ('data-tmpl-ph' in attrs) {
			delete attrs['data-tmpl-ph'];
			model.set?.('attributes', attrs);
		}
	});

	if (opts.readOnly) applyReadOnly(editor);

	return editor;
}

/** Insert a new blank .page after `afterIndex` (driven by the canvas + button via postMessage). */
export function addPage(editor: any, afterIndex: number): void {
	const wrapper = editor.getWrapper?.();
	if (!wrapper) return;
	const pages = wrapper.components().filter((c: any) => c.getClasses?.().includes('page'));
	const insertAt = afterIndex >= 0 ? afterIndex + 1 : pages.length;
	const added = wrapper.components().add('<div class="page"></div>', { at: insertAt });
	// Give the new page its own class so background/etc. styling stays isolated to it.
	const comp = Array.isArray(added) ? added[0] : added;
	if (comp) assignPageClass(comp);
}
