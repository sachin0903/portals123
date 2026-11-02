<script lang="ts">
	import { onMount, flushSync, untrack } from 'svelte';
	import {
		Button,
		ColorInput,
		buttonVariants,
		Label,
		Input,
		SelectMenu,
		Segmented,
		Switch,
		Textarea,
		Checkbox,
		DropdownMenu,
		Popover,
		Command,
		Dialog,
		PdfViewer,
		toast
	} from '@graphene/ui';
	// The style rail. Shared with the email-templates editor rather than app-local: GrapesJS's own
	// style manager is the native widget docs/design-language.md rules out, and suppressing it is
	// only half the job — the other half is knowing which composite owns a declaration and when a
	// value is genuinely SET rather than defaulted. See `create.ts`'s `styleManager: { custom: true }`.
	import { StylePanel, CodeView, type CodeShape } from '@graphene/ui/grapes';
	// The attributes this editor writes, so the code view can colour its own bookkeeping — kept in
	// one place with a test that scans the source, because a stale list fails in the worse
	// direction: a new attribute is simply not coloured, reads as ordinary markup, and gets deleted.
	import { EDITOR_ATTRS } from './editor/marker-attrs';
	// One band of the rail's accordion. Replaces hand-rolled `<button>` + inline-SVG-chevron headers
	// that had drifted in padding and in what they did on keyboard — `Collapsible` owns the button,
	// the aria and the keys, and every band then looks and behaves alike.
	import { InspectorSector } from '@graphene/ui';
	import SaveIcon from '@lucide/svelte/icons/save';
	import CodeXmlIcon from '@lucide/svelte/icons/code-xml';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import Undo2Icon from '@lucide/svelte/icons/undo-2';
	import Redo2Icon from '@lucide/svelte/icons/redo-2';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import FileIcon from '@lucide/svelte/icons/file';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import PaletteIcon from '@lucide/svelte/icons/palette';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import RotateCwIcon from '@lucide/svelte/icons/rotate-cw';
	import XIcon from '@lucide/svelte/icons/x';
	import FilesIcon from '@lucide/svelte/icons/files';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import BoxIcon from '@lucide/svelte/icons/box';
	import BracesIcon from '@lucide/svelte/icons/braces';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import PanelTopIcon from '@lucide/svelte/icons/panel-top';
	import PanelBottomIcon from '@lucide/svelte/icons/panel-bottom';
	import PanelRightIcon from '@lucide/svelte/icons/panel-right';
	import { createEditor, addPage } from './editor/create';
	import {
		watchActivePage,
		scrollToPage,
		getPageComponents,
		movePage,
		duplicatePage,
		authoredClasses
	} from './editor/pages';
	import {
		applyPageLayout,
		applyHeaderFooter,
		defaultLayout,
		applyZoom,
		pageDimensions,
		getPageStyle,
		setPageStyle,
		type PageLayout,
		type PageSizeKey,
		type Orientation
	} from './editor/page-settings';
	import { buildSchema } from './editor/serialize';
	import {
		unsourcedTokens,
		datasetVariableGroups,
		collectionOptions,
		BUILTIN_COLLECTIONS
	} from './editor/variables';
	import { lintJinjaTemplate } from './editor/jinja-table-lint';
	import { parseCssDeclarations } from './editor/css-validate';
	import DatasetDataGrid from '../dataset-authoring/dataset-data-grid.svelte';
	import {
		parseSchema,
		defaultSchema,
		defaultHeader,
		defaultFooter,
		type VarSchema
	} from './editor/types';
	import type { DocumentType } from '@graphene/ui';
	import {
		buildJTemplate,
		parseJTemplate,
		splitExpr,
		joinExpr,
		JINJA_FILTERS,
		availableFilters,
		canAddFilter,
		filterOptgroups,
		defaultJDesign,
		normaliseJDesign,
		tablePreviewInner,
		sampleTableRows,
		DOC_JTABLE_CSS,
		type JColumn,
		type JDesign
	} from './editor/component-types';
	import { CHART_FONTS, previewCompFromAttrs, renderChartSvg } from './editor/chart-preview';

	let {
		name = 'Untitled template',
		documentType = undefined,
		backHref = undefined,
		storageKey = undefined,
		initial = null,
		restore = null,
		formFields = [],
		versions = [],
		datasetOptions = [],
		previewClients = [],
		brandLogo = null,
		resolvePreviewData = undefined,
		renderPreview = undefined,
		renderPreviewPdf = undefined,
		onLoadVersion = undefined,
		onSave = undefined,
		navigate = (href: string) => window.location.assign(href),
		registerBeforeNavigate = undefined,
		readOnly = false
	}: {
		name?: string;
		templateId?: string;
		documentType?: DocumentType;
		description?: string;
		backHref?: string;
		/** The tenant's public logo URL, baked into the Letterhead smart component. */
		brandLogo?: string | null;
		/** localStorage key — the offline draft-recovery cache. */
		storageKey?: string;
		initial?: {
			gjs_project?: unknown;
			html?: string;
			css?: string;
			variable_schema?: unknown;
		} | null;
		/** A previously-saved editor blob to restore (the current version's schema from the API —
		    opaque JSON of PersistBlob shape). Takes precedence over the localStorage cache. */
		restore?: Record<string, unknown> | null;
		/** The template's linked input form's fields (docs/forms.md §9.1, resolved by the page from
		    input_form_code) — the variables usable as {{ tokens }} in the document. */
		formFields?: { key: string; label: string }[];
		/** The template's real version history (newest first), for the History panel. */
		versions?: EditorVersion[];
		/** The tenant's real datasets (code, name, uid, scope and — for static datasets — their column
		    schema), feeding the chart/table config's data-source picker, field pills + live preview. */
		datasetOptions?: {
			code: string;
			name: string;
			columns: string[];
			uid: string;
			scope: string;
			/** Stored sample rows (inline data / authored preview) — used to fill a bound data table
			    on the canvas with real rows instead of literal {{ tokens }}. */
			sample?: { columns: string[]; rows: unknown[][] } | null;
		}[];
		/** Clients (crm.contacts) for the chart preview's subject picker — subject-scoped datasets
		    resolve their rows per client. */
		previewClients?: { contactId: number; name: string }[];
		/** Resolve a dataset's real rows for the chart-config preview (the snapshot the worker renders).
		    Returns column names + row arrays; the editor maps them to objects for the SVG preview. */
		resolvePreviewData?: (
			uid: string,
			contactId: number | null
		) => Promise<{ columns: string[]; rows: unknown[][] }>;
		/** Render the current editor schema to a data-filled sample preview (HTML) for Preview mode;
		    also returns any variables referenced but not in the catalogue (likely typos). */
		renderPreview?: (schema: unknown) => Promise<{
			html: string | null;
			unknownVariables?: { name: string; suggestion?: string | null }[];
		}>;
		/** Render the current schema to a real PDF with the generation engine — the true-to-generation
		    preview surface (pagination/overflow). Falls back to the HTML preview if it rejects. */
		renderPreviewPdf?: (schema: unknown) => Promise<Blob>;
		/** Fetch one version's saved blob on demand (for previewing an older version). */
		onLoadVersion?: (uid: string) => Promise<Record<string, unknown> | null>;
		/** Called with the full editor blob on every Save, so the page can persist it to the API.
		    May return a promise — the Save button stays in its pending state until it settles. */
		onSave?: (blob: PersistBlob) => void | Promise<void>;
		navigate?: (href: string) => void | Promise<void>;
		registerBeforeNavigate?: (
			handler: (navigation: { to: { url: URL } | null; cancel: () => void }) => void
		) => void;
		/** View-only mode for templates:read users without :write. Hides the editing chrome and never
		    autosaves/saves — so a viewer isn't dropped into an editor whose every save 403s. */
		readOnly?: boolean;
	} = $props();

	interface PersistBlob {
		gjs_project: unknown;
		layout: PageLayout;
		schema: VarSchema;
		linkedDatasets: string[];
		// Render-ready export (Jinja-tokenised HTML + CSS + page size), so the server can generate a
		// PDF without a live editor. Populated on save via buildSchema().
		html?: string;
		css?: string;
		pageSize?: { width: string; height: string };
		// The typed chart/table component configs + linked dataset uids. The render worker matches
		// each <g-chart>/<g-table> marker in `html` to its config HERE by data-id — so these MUST be
		// persisted, or every chart/table renders blank in the generated PDF.
		components?: unknown[];
		datasets?: string[];
	}
	// A real server version, as the History panel lists it. The saved blob is fetched on demand
	// (onLoadVersion) when previewing — the panel itself only needs the metadata.
	interface EditorVersion {
		uid: string;
		versionNumber: number;
		status: string; // 'draft' | 'published' | 'archived'
		savedAt: string; // ISO (published_at ?? created_at)
		author: string | null;
	}
	interface DiffStats {
		restored: number;
		changed: number;
	}

	// --- Version diff helpers ---

	function walkProjectComps(project: unknown, cb: (comp: Record<string, unknown>) => void): void {
		if (!project || typeof project !== 'object') return;
		const pages = (project as Record<string, unknown>).pages;
		if (!Array.isArray(pages)) return;
		for (const page of pages) {
			const frames = (page as Record<string, unknown>).frames;
			if (!Array.isArray(frames)) continue;
			for (const frame of frames) {
				walkComp((frame as Record<string, unknown>).component as Record<string, unknown>, cb);
			}
		}
	}

	function walkComp(
		comp: Record<string, unknown> | null | undefined,
		cb: (c: Record<string, unknown>) => void
	): void {
		if (!comp || typeof comp !== 'object') return;
		cb(comp);
		const children = comp.components;
		if (Array.isArray(children)) {
			for (const child of children) walkComp(child as Record<string, unknown>, cb);
		}
	}

	function buildComponentMap(project: unknown): Map<string, string> {
		const map = new Map<string, string>();
		walkProjectComps(project, (comp) => {
			// GrapesJS's live getId() returns attributes.id; serialised JSON has both a top-level
			// `id` (backbone model id) and `attributes.id`. Index under both so the live walk
			// always gets a hit regardless of which one getId() returns.
			const attrs = comp.attributes as Record<string, unknown> | undefined;
			const attrsId = typeof attrs?.id === 'string' && attrs.id ? (attrs.id as string) : undefined;
			const modelId = comp.id as string | undefined;
			const primaryId = attrsId ?? modelId;
			if (!primaryId) return;
			// Fingerprint: own props and attrs excluding id and children
			// eslint-disable-next-line @typescript-eslint/no-unused-vars
			const { id: _id, components: _c, attributes: _a, ...rest } = comp;
			const fingerprint = JSON.stringify({
				...rest,
				attributes: attrs ? { ...attrs, id: undefined } : {}
			});
			map.set(primaryId, fingerprint);
			if (modelId && modelId !== primaryId) map.set(modelId, fingerprint);
		});
		return map;
	}

	/* eslint-disable @typescript-eslint/no-explicit-any -- grapesjs's live editor/component API is dynamically typed */
	function walkEditorComps(editorInstance: any, cb: (comp: any) => void): void {
		function walk(comp: any) {
			try {
				cb(comp);
				comp.components?.()?.each?.((child: any) => walk(child));
			} catch {
				/* skip */
			}
		}
		try {
			const wrapper = editorInstance?.DomComponents?.getWrapper?.();
			if (wrapper) walk(wrapper);
		} catch {
			/* editor not ready */
		}
	}

	function injectDiffHighlights(
		editorInstance: any,
		priorMap: Map<string, string>,
		histMap: Map<string, string>
	): DiffStats {
		const stats: DiffStats = { restored: 0, changed: 0 };
		const highlighted: string[] = [];
		walkEditorComps(editorInstance, (comp) => {
			try {
				const id = comp.getId?.() as string | undefined;
				if (!id || !histMap.has(id)) return;
				const histContent = histMap.get(id)!;
				const priorContent = priorMap.get(id);
				// These two stay literal on purpose: the outline is written onto elements INSIDE the
				// GrapesJS canvas iframe, a separate document that does not inherit the app's CSS
				// custom properties — a var() here resolves to nothing and the cue vanishes.
				let color: string | null = null;
				if (priorContent === undefined) {
					// In historical version but deleted in current → will be restored
					color = '#22c55e';
					stats.restored++;
				} else if (priorContent !== histContent) {
					// Exists in both but content differs → was changed since this version
					color = '#f59e0b';
					stats.changed++;
				}
				if (color) {
					const el = comp.getEl?.() as HTMLElement | null;
					if (el) {
						el.style.setProperty('outline', `2px solid ${color}`, 'important');
						el.style.setProperty('outline-offset', '2px', 'important');
						highlighted.push(id);
					}
				}
			} catch {
				/* skip malformed component */
			}
		});
		diffHighlightedIds = highlighted;
		return stats;
	}

	function clearDiffHighlights(editorInstance: any): void {
		const ids = diffHighlightedIds.slice();
		diffHighlightedIds = [];
		for (const id of ids) {
			try {
				const comp = editorInstance?.DomComponents?.getById?.(id);
				const el = comp?.getEl?.() as HTMLElement | null;
				if (el) {
					el.style.removeProperty('outline');
					el.style.removeProperty('outline-offset');
				}
			} catch {
				/* skip */
			}
		}
	}
	/* eslint-enable @typescript-eslint/no-explicit-any */

	function scheduleDiffInject(priorProject: unknown, histProject: unknown): void {
		const priorMap = buildComponentMap(priorProject);
		const histMap = buildComponentMap(histProject);
		// Give GrapesJS time to re-render the canvas DOM after loadProjectData
		setTimeout(() => {
			if (!editor || !versionPreview) return;
			diffStats = injectDiffHighlights(editor, priorMap, histMap);
		}, 400);
	}

	function loadStored(): PersistBlob | null {
		if (!storageKey || typeof localStorage === 'undefined') return null;
		try {
			const raw = localStorage.getItem(storageKey);
			return raw ? (JSON.parse(raw) as PersistBlob) : null;
		} catch {
			return null;
		}
	}
	function saveStored(): void {
		if (!storageKey || !editor || typeof localStorage === 'undefined') return;
		const blob: PersistBlob = {
			// `?? null` matters: an `undefined` here is DROPPED by JSON.stringify, so the stored
			// version would carry no `gjs_project` key at all and a later reader could not tell a
			// save that had no project from one whose project failed to serialise.
			gjs_project: editor.getProjectData?.() ?? null,
			layout,
			schema,
			linkedDatasets
		};
		try {
			localStorage.setItem(storageKey, JSON.stringify(blob));
		} catch {
			/* quota / unavailable */
		}
	}
	let importFileEl = $state<HTMLInputElement>();

	function exportTemplate(): void {
		if (!editor) return;
		const built = buildSchema(editor, schema, layout);
		const payload = {
			_graphene_template: '1',
			name,
			description: null,
			documentType: documentType ?? 'illustration',
			schema: {
				css: built.css,
				html: built.html,
				datasets: linkedDatasets,
				pageSize: built.pageSize,
				components: built.components,
				gjs_project: built.gjs_project,
				calculations: [],
				variable_schema: schema
			}
		};
		const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `${name.toLowerCase().replace(/\s+/g, '-')}.graphene.json`;
		a.click();
		URL.revokeObjectURL(url);
	}

	function handleImportFile(e: Event): void {
		const file = (e.target as HTMLInputElement).files?.[0];
		if (!file) return;
		const reader = new FileReader();
		reader.onload = (ev) => {
			try {
				const data = JSON.parse(ev.target!.result as string) as Record<string, unknown>;
				if (data._graphene_template !== '1') {
					toast.error('Unrecognised file — expected a Graphene template export.');
					return;
				}
				const s = (data.schema ?? {}) as Record<string, unknown>;
				if (s.gjs_project && editor) {
					editor.loadProjectData(s.gjs_project);
				}
				schema = parseSchema(s.variable_schema ?? {});
				if (Array.isArray(s.datasets)) {
					linkedDatasets = s.datasets as string[];
				}
			} catch {
				toast.error('Failed to read template file — make sure it is a valid JSON export.');
			}
			if (importFileEl) importFileEl.value = '';
		};
		reader.readAsText(file);
	}

	// GrapesJS mount targets (kept mounted; toggled with `hidden` so the editor keeps them).
	let canvasEl = $state<HTMLDivElement>();
	let blocksEl = $state<HTMLDivElement>();
	let layersEl = $state<HTMLDivElement>();
	let traitsEl = $state<HTMLDivElement>();
	let stylesEl = $state<HTMLDivElement>();

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	let editor = $state<any>(null);
	let schema = $state<VarSchema>(defaultSchema());

	// The selected chart/table component (for the Component-tab config form), and a
	// reactive snapshot of its data-comp-* attributes.
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	let selectedComp = $state<any>(null);
	// The chart/table model that "Configure" edits. Usually the selection, but when a chart-frame
	// is selected it's the locked chart INSIDE it (the frame is the selectable/stylable unit; the
	// chart is the thing you configure). null when the selection has no chart/table config.
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	let chartComp = $state<any>(null);
	// Tenant chart palette — resolved from the live theme CSS vars (this tenant's white-label
	// branding) to concrete rgb() on mount (see resolveCssColour below), so charts + the "Tenant
	// colours" swatches track the tenant. Brand + primary lead, then a categorical spread.
	let tenantPalette = $state<string[]>([
		'#0f766e',
		'#1e3a5f',
		'#3b82f6',
		'#f59e0b',
		'#8b5cf6',
		'#10b981',
		'#ef4444',
		'#ec4899'
	]);
	// Bumped on any live canvas edit (cell text, add/remove, design) so the sidebar table preview
	// re-reads the selected component's current HTML. Cheap: it's only read by preview deriveds.
	let editorTick = $state(0);
	// Bumped whenever the STYLE models may have moved, so `StylePanel` re-reads them. Separate from
	// `editorTick` on purpose: that one drives preview deriveds that serialise the canvas, and the
	// style rail must re-read on events those do not care about (`style:target` when the selection
	// resolves, undo/redo) while NOT paying for a re-serialise every time a property is nudged.
	let styleRevision = $state(0);
	// True while a text component is in RTE edit mode (between rte:enable/rte:disable). Serialising
	// the canvas (buildSchema → getWrapper().toHTML()) while RTE is live forces GrapesJS to sync the
	// active element, yanking the caret to the start — so the live lint must NOT serialise mid-edit.
	let rteActive = $state(false);
	// Multi-select (GrapesJS supports ctrl/shift-click): the panels target the primary
	// (`selectedComp`); the target bar lists every selected component by name.
	let selectedCount = $state(0);
	let selectedNames = $state<string[]>([]);
	let compAttrs = $state<Record<string, string>>({});
	// When a jinja-var span is selected, we render our own Svelte select instead
	// of GrapesJS's built-in trait widget (which inherits the dark theme).
	let jinjaVarKey = $state<string | null>(null);

	// A page (doc-page) is selectable, so selecting one surfaces the page settings in
	// the right rail (folded in from the old "Page" tab) instead of the generic
	// component config. Detected by the `page` class the doc-page type carries.
	const isPageSelected = $derived(
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		!!selectedComp && ((selectedComp as any).getClasses?.().includes('page') ?? false)
	);
	// Index of the SELECTED page component (not the scroll-tracked activePage) — the page
	// settings + header/footer editor act on whichever page is actually selected.
	const selectedPageIndex = $derived.by(() => {
		if (!isPageSelected || !editor || !selectedComp) return activePage;
		const idx = getPageComponents(editor).indexOf(selectedComp);
		return idx >= 0 ? idx : activePage;
	});

	// ── Bespoke page-settings panel ──────────────────────────────────────────────
	// The doc-page's paper-size / orientation / margin props are edited through our own
	// shadcn controls rather than GrapesJS's (washed-out) trait widgets. pageDraft mirrors
	// the selected page's props; writing a control sets the prop (which fires
	// component:update → syncLayoutFromPage → applyPageLayout) and updates the mirror.
	const PAGE_SIZE_OPTS: [PageSizeKey, string][] = [
		['A4', 'A4'],
		['A5', 'A5'],
		['A3', 'A3'],
		['Letter', 'Letter'],
		['Legal', 'Legal']
	];
	const ORIENTATION_OPTS: [Orientation, string][] = [
		['portrait', 'Portrait'],
		['landscape', 'Landscape']
	];
	const MARGIN_FIELDS: {
		prop: 'marginTop' | 'marginRight' | 'marginBottom' | 'marginLeft';
		label: string;
	}[] = [
		{ prop: 'marginTop', label: 'Top' },
		{ prop: 'marginRight', label: 'Right' },
		{ prop: 'marginBottom', label: 'Bottom' },
		{ prop: 'marginLeft', label: 'Left' }
	];
	let pageDraft = $state<{
		pageSize: PageSizeKey;
		pageOrientation: Orientation;
		marginTop: number;
		marginRight: number;
		marginBottom: number;
		marginLeft: number;
	}>({
		pageSize: 'A4',
		pageOrientation: 'portrait',
		marginTop: 19,
		marginRight: 19,
		marginBottom: 19,
		marginLeft: 19
	});
	$effect(() => {
		// Repopulate whenever the selected page changes (Backbone reads aren't tracked, so the
		// effect keys off the reactive selectedComp / isPageSelected).
		if (!isPageSelected || !selectedComp) return;
		pageDraft = {
			pageSize: (selectedComp.get('pageSize') as PageSizeKey) ?? 'A4',
			pageOrientation: (selectedComp.get('pageOrientation') as Orientation) ?? 'portrait',
			marginTop: Number(selectedComp.get('marginTop') ?? 19),
			marginRight: Number(selectedComp.get('marginRight') ?? 19),
			marginBottom: Number(selectedComp.get('marginBottom') ?? 19),
			marginLeft: Number(selectedComp.get('marginLeft') ?? 19)
		};
	});
	function setPageChoice(prop: 'pageSize' | 'pageOrientation', value: string): void {
		if (!selectedComp) return;
		selectedComp.set(prop, value);
		if (prop === 'pageSize') pageDraft.pageSize = value as PageSizeKey;
		else pageDraft.pageOrientation = value as Orientation;
	}
	function setPageMargin(
		prop: 'marginTop' | 'marginRight' | 'marginBottom' | 'marginLeft',
		value: number,
		opts?: { avoidStore?: boolean }
	): void {
		if (!selectedComp) return;
		const v = Math.max(0, Number.isFinite(value) ? value : 0);
		// avoidStore skips the undo record — used for live drag frames, which are collapsed into a
		// single before→after step on release (see startMarginDrag). Direct edits omit it (tracked).
		selectedComp.set(prop, v, opts);
		pageDraft[prop] = v;
	}

	// Visual margins editor — a proportional page preview whose four edges drag to set the
	// selected page's margins (mirrors the pre-refactor diagram, now wired to pageDraft).
	let diagEl = $state<HTMLDivElement | undefined>(undefined);
	let marginDrag = $state<{ side: 0 | 1 | 2 | 3; startPx: number; startMm: number } | null>(null);
	const pageLayoutDraft = $derived<PageLayout>({
		size: pageDraft.pageSize,
		orientation: pageDraft.pageOrientation,
		margins: [
			pageDraft.marginTop,
			pageDraft.marginRight,
			pageDraft.marginBottom,
			pageDraft.marginLeft
		] as [number, number, number, number]
	});
	const pdDiag = $derived(pageDimensions(pageLayoutDraft));
	// Fit the preview within 160×220px while preserving exact page proportions.
	const diagH = $derived(Math.min(160, Math.round((220 * pdDiag.h) / pdDiag.w)));
	const diagW = $derived(Math.round((diagH * pdDiag.w) / pdDiag.h));
	const marginPct = $derived({
		t: (pageLayoutDraft.margins[0] / pdDiag.h) * 100,
		r: (pageLayoutDraft.margins[1] / pdDiag.w) * 100,
		b: (pageLayoutDraft.margins[2] / pdDiag.h) * 100,
		l: (pageLayoutDraft.margins[3] / pdDiag.w) * 100
	});
	// Quick margin presets (mm, applied to all four sides) — saves setting four fields by hand.
	const MARGIN_PRESETS: { label: string; mm: number }[] = [
		{ label: 'None', mm: 0 },
		{ label: 'Narrow', mm: 12 },
		{ label: 'Normal', mm: 19 },
		{ label: 'Wide', mm: 25 }
	];
	function setAllMargins(mm: number): void {
		setPageMargin('marginTop', mm);
		setPageMargin('marginRight', mm);
		setPageMargin('marginBottom', mm);
		setPageMargin('marginLeft', mm);
	}
	// side 0=top 1=right 2=bottom 3=left → the matching margin prop.
	const MARGIN_PROP_BY_SIDE = ['marginTop', 'marginRight', 'marginBottom', 'marginLeft'] as const;
	function setMarginSide(
		side: 0 | 1 | 2 | 3,
		value: number,
		opts?: { avoidStore?: boolean }
	): void {
		setPageMargin(MARGIN_PROP_BY_SIDE[side], Math.round(value * 100) / 100, opts);
	}
	function startMarginDrag(e: PointerEvent, side: 0 | 1 | 2 | 3): void {
		e.preventDefault();
		const startPx = side === 0 || side === 2 ? e.clientY : e.clientX;
		marginDrag = { side, startPx, startMm: pageLayoutDraft.margins[side] };
		// Collapse the whole drag into ONE undo step (mirrors the canvas box-model handler): pause the
		// UndoManager and apply each move untracked, then commit a single before→after change on release.
		const prop = MARGIN_PROP_BY_SIDE[side];
		const beforeMm = marginDrag.startMm;
		const onMove = (ev: PointerEvent) => {
			if (!marginDrag || !diagEl) return;
			const { w: pageW, h: pageH } = pageDimensions(pageLayoutDraft);
			const delta =
				(marginDrag.side === 0 || marginDrag.side === 2 ? ev.clientY : ev.clientX) -
				marginDrag.startPx;
			const mmPerPx =
				marginDrag.side === 0 || marginDrag.side === 2
					? pageH / diagEl.clientHeight
					: pageW / diagEl.clientWidth;
			// top/left: drag toward the page centre increases the margin; bottom/right invert.
			const signed = marginDrag.side === 0 || marginDrag.side === 3 ? delta : -delta;
			const opposite =
				marginDrag.side === 0 ? 2 : marginDrag.side === 2 ? 0 : marginDrag.side === 1 ? 3 : 1;
			const maxMm =
				(marginDrag.side === 0 || marginDrag.side === 2 ? pageH : pageW) -
				pageLayoutDraft.margins[opposite] -
				10;
			editor?.UndoManager?.stop?.();
			setMarginSide(
				marginDrag.side,
				Math.max(0, Math.min(maxMm, marginDrag.startMm + signed * mmPerPx)),
				{ avoidStore: true }
			);
		};
		const onUp = () => {
			// Record the net change as one step: revert (untracked) to the pre-drag value, resume
			// tracking, then re-apply the final value so the UndoManager logs one before→after change.
			const finalMm = selectedComp ? Number(selectedComp.get(prop) ?? beforeMm) : beforeMm;
			if (selectedComp && finalMm !== beforeMm) {
				selectedComp.set(prop, beforeMm, { avoidStore: true });
				editor?.UndoManager?.start?.();
				setPageMargin(prop, finalMm);
			} else {
				editor?.UndoManager?.start?.();
			}
			marginDrag = null;
			window.removeEventListener('pointermove', onMove);
			window.removeEventListener('pointerup', onUp);
		};
		window.addEventListener('pointermove', onMove);
		window.addEventListener('pointerup', onUp);
	}

	// Header/footer editor — sub-tab + selected column, per active page.
	let hfSub = $state<'header' | 'footer'>('header');
	let hfSelCol = $state(0);
	$effect(() => {
		// Reset the column selection when switching page or header/footer sub-tab.
		void selectedPageIndex;
		void hfSub;
		hfSelCol = 0;
	});

	// ── Image (image) editor ──────────────────────────────────────────────────────
	// A selected <image> exposes its unique settings (Alt text, object-fit, rounded
	// corners) through bespoke shadcn controls — mirroring the page panel — instead of the
	// washed-out GrapesJS trait widgets. `alt` is an HTML attribute; objectFit / rounded are
	// changeProp model props whose change handlers restyle the element (see component-types.ts).
	const isImageSelected = $derived<boolean>(selectedComp?.get?.('type') === 'image');
	let imageSetupOpen = $state(true);
	const IMAGE_FIT_OPTS: [string, string][] = [
		['', 'Default'],
		['cover', 'Cover'],
		['contain', 'Contain'],
		['fill', 'Fill'],
		['none', 'None']
	];
	let imageDraft = $state<{ alt: string; objectFit: string; rounded: boolean }>({
		alt: '',
		objectFit: '',
		rounded: false
	});
	$effect(() => {
		// Repopulate whenever a different image is selected (Backbone reads aren't tracked, so the
		// effect keys off the reactive selectedComp / isImageSelected).
		if (!isImageSelected || !selectedComp) return;
		const attrs = (selectedComp.getAttributes?.() ?? {}) as Record<string, string>;
		imageDraft = {
			alt: attrs.alt ?? '',
			objectFit: (selectedComp.get('objectFit') as string) ?? '',
			rounded: !!selectedComp.get('rounded')
		};
	});
	function setImageAlt(value: string): void {
		if (!selectedComp) return;
		selectedComp.addAttributes?.({ alt: value });
		imageDraft.alt = value;
	}
	function setImageFit(value: string): void {
		if (!selectedComp) return;
		selectedComp.set('objectFit', value); // change:objectFit → updateFit (object-fit style)
		imageDraft.objectFit = value;
	}
	function setImageRounded(value: boolean): void {
		if (!selectedComp) return;
		selectedComp.set('rounded', value); // change:rounded → updateRounded (border-radius style)
		imageDraft.rounded = value;
	}

	// ── Jinja table (doc-jtable) editor ──────────────────────────────────────────
	// A selected doc-jtable is edited via a bespoke panel: VISUAL (dataset + columns,
	// regenerates the {% for %} template) or RAW (edit the jinja/HTML source directly).
	const isJtable = $derived<boolean>(selectedComp?.get?.('type') === 'doc-jtable');
	// A static text table (doc-texttable): typed directly on the canvas; rows/columns are
	// added/removed from the panel below. Cells are RTE-editable, so no config modal. Clicking
	// a cell selects the cell (to type) — walk up to the owning table so the grid controls stay.
	const textTableComp = $derived.by(() => {
		let c = selectedComp;
		while (c) {
			if (c.get?.('type') === 'doc-texttable') return c;
			c = c.parent?.();
		}
		return null;
	});
	const isTextTable = $derived<boolean>(!!textTableComp);
	function ttCall(method: 'addRow' | 'removeRow' | 'addColumn' | 'removeColumn'): void {
		textTableComp?.[method]?.();
	}
	let jtMode = $state<'visual' | 'raw'>('visual');
	let jtDataset = $state('items');
	let jtColumns = $state<JColumn[]>([]);
	let jtRaw = $state('');
	// The raw template doesn't match the generated shape — visual mode can't represent it.
	let jtCustom = $state(false);
	// Structural warnings for the raw template (unbalanced delimiters / unclosed blocks) — a live
	// authoring nudge shown under the Raw editor (jinja-table-lint).
	const jtRawLint = $derived(lintJinjaTemplate(jtRaw));
	let jtableConfigOpen = $state(false); // the spacious table config modal
	let jtDesign = $state<JDesign>(defaultJDesign()); // per-table visual design
	// Transient values for the "add filter" pickers (SelectMenu) — reset to '' after each pick so
	// the trigger falls back to its placeholder rather than showing the last-added filter.
	let jtFilterPick = $state('');
	let jinjaFilterPick = $state('');
	$effect(() => {
		// Repopulate the draft whenever a different table is selected.
		if (!isJtable || !selectedComp) return;
		const tmpl = (selectedComp.get('jtemplate') as string) || '';
		jtRaw = tmpl;
		jtDesign = normaliseJDesign(selectedComp.get('jdesign'));
		const parsed = parseJTemplate(tmpl);
		if (parsed) {
			jtDataset = parsed.dataset;
			jtColumns = parsed.columns;
			jtCustom = false;
		} else {
			jtCustom = true;
		}
	});
	// Merge a design change onto the draft + the component (change:jdesign re-renders the table).
	function jtSetDesign(patch: Partial<JDesign>): void {
		jtDesign = { ...jtDesign, ...patch };
		selectedComp?.set('jdesign', { ...jtDesign });
	}
	function jtResetDesign(): void {
		jtDesign = defaultJDesign();
		selectedComp?.set('jdesign', { ...jtDesign });
	}
	// Transparent header background: `type=color` can't express it, so a toggle drives the value
	// (`transparent` flows straight through jdesignTableAttrs → `--jt-header-bg`). Off restores grey.
	const headerTransparent = $derived(jtDesign.headerBg === 'transparent');
	function toggleHeaderTransparent(): void {
		jtSetDesign({ headerBg: headerTransparent ? '#f1f5f9' : 'transparent' });
	}
	// Same for the body-row background (default transparent so the page shows through).
	const rowTransparent = $derived(jtDesign.rowBg === 'transparent');
	function toggleRowTransparent(): void {
		jtSetDesign({ rowBg: rowTransparent ? '#ffffff' : 'transparent' });
	}
	// Select the whole table component (from a cell/inner selection) so it can be styled, moved,
	// duplicated or deleted as a unit — clicking a table otherwise lands on an inner cell.
	function selectWholeTable(comp: unknown): void {
		if (comp) editor?.select?.(comp);
	}
	// Styled sidebar previews (same .doc-jtable-inner contract as canvas/PDF). Data table: headers +
	// sample rows from the columns. Text table: the component's actual cells, re-read on editorTick.
	// The `<style>` carrying DOC_JTABLE_CSS is assembled here (in the script, where it's inert) — a
	// literal <style> tag in the markup would derail Svelte's template parser.
	const jtPreviewStyleTag = `<style>${DOC_JTABLE_CSS}</style>`;
	const jtablePreviewHtml = $derived.by(() => {
		if (!isJtable || !jtColumns.length) return '';
		const headers = jtColumns.map((c) => c.header || '—');
		const bases = jtColumns.map((c) => splitExpr(c.expr).base);
		return (
			jtPreviewStyleTag +
			tablePreviewInner(
				jtDesign,
				headers,
				sampleTableRows(bases, 3),
				jtColumns.map((c) => ({
					width: Number(c.width) > 0 ? `${Number(c.width)}%` : '',
					align: c.align || 'left'
				}))
			)
		);
	});
	const textTablePreviewHtml = $derived.by(() => {
		void editorTick; // re-read on live edits (cell text, add/remove, design)
		if (!isTextTable || !textTableComp) return '';
		try {
			const inner = (textTableComp.tableComp?.()?.toHTML?.() as string) ?? '';
			return inner ? jtPreviewStyleTag + inner : '';
		} catch {
			return '';
		}
	});
	const JT_BORDER_OPTS = [
		{ value: 'all', label: 'All' },
		{ value: 'rows', label: 'Rows' },
		{ value: 'none', label: 'None' }
	] as const;
	const JT_ALIGN_OPTS = [
		{ value: 'left', label: 'Left' },
		{ value: 'center', label: 'Centre' },
		{ value: 'right', label: 'Right' }
	] as const;
	// Push the structured (visual) form back onto the component as a regenerated template.
	function applyJtableVisual(): void {
		if (!selectedComp) return;
		const tmpl = buildJTemplate(jtDataset, jtColumns);
		jtRaw = tmpl;
		jtCustom = false;
		selectedComp.set('jtemplate', tmpl);
	}
	function jtSetDataset(v: string): void {
		jtDataset = v;
		// Binding a table to a dataset means the template pulls it in — keep linked_datasets in step
		// so it's remembered + offered as "in this template".
		addLinkedDataset(v);
		applyJtableVisual();
	}
	function jtAddColumn(): void {
		jtColumns = [...jtColumns, { header: 'Column', expr: 'row.field' }];
		applyJtableVisual();
	}
	function jtRemoveColumn(i: number): void {
		jtColumns = jtColumns.filter((_, j) => j !== i);
		applyJtableVisual();
	}
	// Drag-to-reorder columns (grip handle initiates; each row is a drop target).
	let jtDragFrom = $state<number | null>(null);
	let jtDragOver = $state<number | null>(null);
	function jtMoveColumn(from: number, to: number): void {
		if (from === to || from < 0 || to < 0 || from >= jtColumns.length || to >= jtColumns.length)
			return;
		const cols = [...jtColumns];
		const [moved] = cols.splice(from, 1);
		cols.splice(to, 0, moved!);
		jtColumns = cols;
		applyJtableVisual();
	}
	function jtColDragStart(e: DragEvent, i: number): void {
		jtDragFrom = i;
		if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
	}
	function jtColDragOver(e: DragEvent, i: number): void {
		e.preventDefault();
		if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
		jtDragOver = i;
	}
	function jtColDrop(e: DragEvent, i: number): void {
		e.preventDefault();
		if (jtDragFrom !== null) jtMoveColumn(jtDragFrom, i);
		jtDragFrom = null;
		jtDragOver = null;
	}
	function jtColDragEnd(): void {
		jtDragFrom = null;
		jtDragOver = null;
	}
	function jtSetHeader(i: number, v: string): void {
		jtColumns = jtColumns.map((c, j) => (j === i ? { ...c, header: v } : c));
		applyJtableVisual();
	}
	/** A column's WIDTH, as a percentage, blank for automatic. Round-tripped through the template's
	 *  own `<colgroup>` (`buildJTemplate`/`parseJTemplate`), so the authored HTML stays the single
	 *  source of truth and cannot disagree with this list. */
	function jtSetWidth(i: number, v: string): void {
		jtColumns = jtColumns.map((c, j) => (j === i ? { ...c, width: v } : c));
		applyJtableVisual();
	}
	function jtSetAlign(i: number, v: string): void {
		jtColumns = jtColumns.map((c, j) => (j === i ? { ...c, align: v } : c));
		applyJtableVisual();
	}
	function jtSetExpr(i: number, v: string): void {
		jtColumns = jtColumns.map((c, j) => (j === i ? { ...c, expr: v } : c));
		applyJtableVisual();
	}
	// Column value expression = base + filter pipeline, edited separately (base input + chips).
	function jtSetBase(i: number, base: string): void {
		jtSetExpr(i, joinExpr(base, splitExpr(jtColumns[i]?.expr ?? '').filters));
	}
	function jtAddFilter(i: number, snippet: string): void {
		if (!snippet) return;
		const { base, filters } = splitExpr(jtColumns[i]?.expr ?? '');
		if (!canAddFilter(filters, snippet)) return; // blocks duplicates + group conflicts
		jtSetExpr(i, joinExpr(base, [...filters, snippet]));
	}
	function jtRemoveFilter(i: number, fi: number): void {
		const { base, filters } = splitExpr(jtColumns[i]?.expr ?? '');
		jtSetExpr(
			i,
			joinExpr(
				base,
				filters.filter((_, k) => k !== fi)
			)
		);
	}

	// ── Jinja text variable (chip) value = base variable + filter pipeline ─────────
	// Mirrors the table column editor so filters work the same way in text (and anywhere
	// else the variable picker appears). Writes the combined `base | filters` to the chip's
	// data-jinja-key (which renders {{ base | filters }}) or the DOM-only token.
	function pushJinjaKey(fullKey: string): void {
		jinjaVarKey = fullKey;
		if (selectedComp) {
			selectedComp.addAttributes?.({ 'data-jinja-key': fullKey });
		} else {
			try {
				const iframe = editor?.Canvas?.getFrameEl?.() as HTMLIFrameElement | null;
				iframe?.contentWindow?.postMessage({ type: 'gjs-jinja-update', key: fullKey }, '*');
			} catch {
				/* canvas not ready */
			}
		}
	}
	function jinjaSetBase(base: string): void {
		pushJinjaKey(joinExpr(base, splitExpr(jinjaVarKey ?? '').filters));
	}
	function jinjaAddFilter(snippet: string): void {
		if (!snippet) return;
		const { base, filters } = splitExpr(jinjaVarKey ?? '');
		if (!canAddFilter(filters, snippet)) return; // blocks duplicates + group conflicts
		pushJinjaKey(joinExpr(base, [...filters, snippet]));
	}
	function jinjaRemoveFilter(fi: number): void {
		const { base, filters } = splitExpr(jinjaVarKey ?? '');
		pushJinjaKey(
			joinExpr(
				base,
				filters.filter((_, k) => k !== fi)
			)
		);
	}
	// Apply raw edits, then re-derive the structured form if it still matches.
	function applyJtableRaw(): void {
		if (!selectedComp) return;
		selectedComp.set('jtemplate', jtRaw);
		const parsed = parseJTemplate(jtRaw);
		if (parsed) {
			jtDataset = parsed.dataset;
			jtColumns = parsed.columns;
			jtCustom = false;
		} else {
			jtCustom = true;
		}
	}
	// Dataset connection: jtDatasetCols (the known columns for the chosen dataset) is derived
	// below from the real datasetOptions. Used for "Prefill" + expression suggestions.
	const titleiseField = (f: string): string =>
		f.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
	// Replace the columns with one per field of the selected dataset (header titled, expr bound).
	function jtPrefillFromDataset(): void {
		if (!jtDatasetCols.length) return;
		jtColumns = jtDatasetCols.map((f) => ({ header: titleiseField(f), expr: `row.${f}` }));
		applyJtableVisual();
	}

	// Component-tab target indicator (read-only) — shows what the styles below apply to.
	const targetName = $derived<string>(selectedComp?.getName?.() ?? '');
	const targetTag = $derived<string>(
		selectedComp ? String(selectedComp.get?.('tagName') ?? '').toLowerCase() : ''
	);
	// `authoredClasses`, not the raw list: the editor's own `page`/`pg-…` are not style targets
	// an author can write a rule against (see pages.ts).
	const targetClasses = $derived<string[]>(authoredClasses(selectedComp?.getClasses?.() ?? []));
	// With more than one component selected we only expose the common controls (the Style
	// manager, which applies to all of them) and hide the primary's type-specific context
	// (traits, chart config, the jinja-var picker, the primary-only CSS box).
	const multiSelect = $derived(selectedCount > 1);
	// Types that carry no relevant style sectors (dividers are trait-only; logic/structural
	// types and saved markers have no visual box) set `noStyle` — hide the whole Style panel.
	const hideStyle = $derived<boolean>(!!selectedComp?.get?.('noStyle'));

	// Page-only right-rail sections collapse like the Style sectors / left-rail accordions.
	let pageSetupOpen = $state(false);
	let headerFooterOpen = $state(false);

	// CSS mode — swaps the visual Style manager for an editable CSS box scoped to the
	// selected element's own rule. Two-way: applying parses the declarations back onto the
	// component (setStyle), and the visual controls reflect the result on return.
	let cssMode = $state(false);

	// ── The `</>` code pane ──────────────────────────────────────────────────────────────────────
	// Over the canvas, not in the rail: it is the WHOLE document, where the rail is about whatever
	// is selected. Read-only until deliberately unlocked — see `CodeView`.
	let codeOpen = $state(false);
	let codeShape = $state<CodeShape>('source');
	/**
	 * The two documents the pane can show.
	 *
	 * `source` is the canvas's own HTML, carrying the `data-*` this editor round-trips (conditions,
	 * loops, chart config). `output` is what that COMPILES to — the Jinja template the renderer is
	 * handed — which is derived, so it has nowhere to write a hand edit back to and the pane offers
	 * no unlock for it.
	 *
	 * Read on demand rather than kept in state: both are a serialisation of the live canvas, and a
	 * cached copy is a copy that can be stale while looking authoritative. `editorTick` is taken as
	 * a dependency so the pane follows edits made behind it.
	 */
	const codeHtml = $derived.by(() => {
		void editorTick;
		if (!editor || !codeOpen) return '';
		try {
			return codeShape === 'output'
				? buildSchema(editor, schema, layout).html
				: (editor.getWrapper?.()?.toHTML?.() ?? '');
		} catch {
			// A compile can throw on a half-authored condition. The pane says so rather than going
			// blank, which would read as "this document is empty".
			return '<!-- This document cannot be compiled yet — finish the condition being edited. -->';
		}
	});
	let cssText = $state('');
	let cssErrors = $state<string[]>([]);
	$effect(() => {
		// (Re)populate the box when entering CSS mode or when the selection changes while in it.
		if (!cssMode || !selectedComp) return;
		const style = (selectedComp.getStyle?.() ?? {}) as Record<string, string>;
		cssText = Object.entries(style)
			.filter(([k]) => !k.startsWith('__'))
			.map(([k, v]) => `${k}: ${v};`)
			.join('\n');
	});
	function applyCss(): void {
		if (!selectedComp) return;
		// Parse + validate (the paren/quote-aware split lives in parseCssDeclarations, so a
		// `url("…;base64,…")` value survives). A malformed declaration is surfaced and the WHOLE
		// apply is skipped — the element's style is left unchanged rather than silently dropping the
		// bad bits (the old behaviour).
		const { style, errors } = parseCssDeclarations(cssText);
		cssErrors = errors;
		if (errors.length) return;
		// setStyle replaces the element's own declarations with the edited set (removing any the
		// user deleted) in a single undo step, keeping the CSS box authoritative while it's open.
		selectedComp.setStyle?.(style);
	}
	/**
	 * Replace the canvas with hand-edited HTML from the code pane.
	 *
	 * `setComponents` reparses, so GrapesJS rebuilds its component tree from the markup — which is
	 * exactly what makes a hand edit real, and also why the editor's own `data-*` must survive it:
	 * an attribute dropped here is a condition or a chart that the panel can no longer see. The pane
	 * says so before it unlocks; this does not second-guess it, because refusing an edit an author
	 * confirmed would make the unlock a lie.
	 */
	function applyHandEditedHtml(html: string): void {
		if (!editor || readOnly) return;
		editor.setComponents?.(html);
		editorTick++;
		styleRevision++;
		scheduleAutosave();
	}

	function toggleCssMode(): void {
		if (cssMode) applyCss(); // commit edits before returning to the visual controls
		cssMode = !cssMode;
	}

	// Left rail view: 'outline' shows Pages + Layers, 'blocks' shows the block palette.
	// Toggled by the + / × button in the rail header (no tab strip).
	let leftView = $state<'outline' | 'blocks'>('outline');
	// Whether the style rail is showing — a view preference, never persisted, the email editor's
	// `railOpen`. Shut, the rail is one arrow tab on the canvas's right edge; nothing is reserved for
	// it either way, so the page does not move when it goes.
	let railOpen = $state(true);
	let pagesOpen = $state(true);
	let layersOpen = $state(true);
	let savedAt = $state<string | null>(null);

	// Whether the document has any content yet (all pages empty). Drives the canvas empty-state and the
	// one-time auto-open of the block palette for a fresh template. Recomputed on editorTick because
	// Backbone reads aren't tracked.
	const canvasEmpty = $derived.by(() => {
		void editorTick;
		if (!editor) return false;
		try {
			return !getPageComponents(editor).some((p) => (p.components?.()?.length ?? 0) > 0);
		} catch {
			return false;
		}
	});
	let autoOpenedBlocks = false;
	// The empty-page starting hint is a dismissible popup. Once it's been dismissed OR the document has
	// ever had content, it never comes back for this template (persisted in localStorage), so it can't
	// reappear if the page is later emptied or on reload.
	let emptyStateDismissed = $state(false);
	const emptyHintKey = $derived(storageKey ? `${storageKey}:empty-hint` : null);
	function dismissEmptyHint(): void {
		emptyStateDismissed = true;
		try {
			if (emptyHintKey) localStorage.setItem(emptyHintKey, '1');
		} catch {
			/* storage unavailable */
		}
	}
	let emptyHintRestored = false;
	$effect(() => {
		// Restore the persisted "seen it" flag once the editor is up, then mark it seen the moment the
		// document has any content (so it won't return after the page is emptied again).
		if (!emptyHintRestored) {
			emptyHintRestored = true;
			try {
				if (emptyHintKey && localStorage.getItem(emptyHintKey)) emptyStateDismissed = true;
			} catch {
				/* storage unavailable */
			}
		}
		if (editor && !canvasEmpty && !emptyStateDismissed) dismissEmptyHint();
	});
	// Page indices whose content is taller than one sheet (reported from the canvas — grapesjs-jinja-
	// vars.js). Surfaced as an in-editor overflow indicator so the author sees it before preview/generate.
	let overflowPages = $state<number[]>([]);
	$effect(() => {
		// Once, after the document first loads (editorTick > 0): if it's empty, open the block palette
		// so a fresh template has an obvious starting point. Never fights a later manual view change.
		if (autoOpenedBlocks || readOnly || !editor || editorTick === 0) return;
		autoOpenedBlocks = true;
		if (canvasEmpty) leftView = 'blocks';
	});

	let activePage = $state(0);

	/**
	 * What the right rail says it is acting on.
	 *
	 * Named rather than shown as a chip row, because the head has to answer "what will this edit"
	 * in one glance and a multi-selection has no single name. Falls back through the states that
	 * genuinely differ: a page is a component but reads as a page, a jinja variable has no element
	 * name of its own, and nothing selected means the rail is showing page-level settings.
	 */
	const railTitle = $derived<string>(
		multiSelect
			? `${selectedCount} elements`
			: isPageSelected
				? `Page ${activePage + 1}`
				: jinjaVarKey !== null
					? 'Variable'
					: targetName || (selectedComp ? 'Element' : 'Page settings')
	);
	let pageCount = $state(1);
	let pageNames = $state<string[]>([]);

	// Page layout (applied to the canvas via injected style; see page-settings.ts).
	let layout = $state(defaultLayout());
	let zoom = $state(100);
	let themeOpen = $state(false);
	let historyOpen = $state(false);
	let datasetSectionOpen = $state(false);
	// Canvas right-click context menu. `comp` is the GrapesJS component under the cursor
	// (null when the click landed on empty canvas / the page body).
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	let ctxMenu = $state<{ x: number; y: number; comp: any } | null>(null);
	let ctxMenuEl = $state<HTMLDivElement>();
	let ctxBlockGroups = $state<
		{ label: string; blocks: { id: string; label: string; media: string }[] }[]
	>([]);
	// The History panel lists the real server versions (newest first), passed from the page.
	const historyEntries = $derived(versions);
	let versionPreview = $state<{ version: EditorVersion; priorBlob: PersistBlob } | null>(null);
	let diffStats = $state<DiffStats | null>(null);
	let diffHighlightedIds = $state<string[]>([]);
	let dragFromIndex = $state<number | null>(null);
	let dragOverIndex = $state<number | null>(null);

	// Margin drag

	onMount(() => {
		let alive = true;
		let onMessage: ((e: MessageEvent) => void) | null = null;
		let stopWatch: (() => void) | null = null;
		let ctxTeardown: (() => void) | null = null;
		let onCanvasWheel: ((e: WheelEvent) => void) | null = null;
		// In-flight box-model drag gesture (size/margin/padding), used to collapse the
		// per-mousemove style updates into a single undo step. `comp` is only identity-compared.
		let boxDrag: { comp: unknown; prop: string; before: string | number } | null = null;

		// Server blob (restore) is authoritative; the localStorage cache is a fallback.
		const stored = (restore as PersistBlob | null) ?? loadStored();
		if (stored) {
			// Run through parseSchema so any fields added since the data was saved (e.g. header)
			// are back-filled with their defaults rather than left undefined.
			if (stored.schema) schema = parseSchema(stored.schema);
			if (stored.layout) layout = stored.layout;
			if (stored.linkedDatasets) linkedDatasets = stored.linkedDatasets;
		} else if (initial?.variable_schema) {
			schema = parseSchema(initial.variable_schema);
		}
		// Hand `loadInto` EVERYTHING the blob has and let it choose. It already prefers the GrapesJS
		// project and falls back to the saved HTML/CSS — but this line used to gate on `gjs_project`
		// alone, so a version that had `html` and no usable project blob fell through to `initial`
		// (nothing) and the canvas opened BLANK on a template that plainly has content. The saved
		// HTML is not a lesser copy: it is what the API previews and what the worker generates the
		// PDF from, so it is the last thing that should be discarded.
		//
		// `gjs_project` goes missing for real: `save()` writes `editor.getProjectData?.()` with no
		// `?? null`, and `JSON.stringify` DROPS a key whose value is `undefined` — so any save made
		// while that optional call short-circuited stored html/css and no project at all.
		const initialData = stored
			? { gjs_project: stored.gjs_project, html: stored.html, css: stored.css }
			: initial;

		(async () => {
			// Both failure modes below used to be SILENT, and they produce the same screen: GrapesJS
			// never initialises, so `editor` stays null — and every derived readout then reports its
			// own default rather than a fault. `pageCount` is `$state(1)`, so the rail says
			// "Pages (1)"; `canvasEmpty` returns FALSE when there is no editor, so neither the
			// empty-page hint nor the auto-opened block palette appears. The result is an editor that
			// looks loaded, names a page that does not exist, and draws nothing — with nothing
			// anywhere saying why.
			if (!canvasEl || !blocksEl || !layersEl || !traitsEl || !stylesEl) {
				initError = 'The editor could not attach to the page. Reloading usually fixes it.';
				return;
			}
			const ed = await createEditor({
				container: canvasEl,
				blocksEl,
				layersEl,
				traitsEl,
				stylesEl,
				getSchema: () => schema,
				// A data table renders its bound dataset's real sample rows on the canvas (so binding a
				// source actually changes what's shown). Resolve the sample by dataset code from the
				// datasets the page loaded.
				getDatasetSample: (code: string) =>
					datasetOptions.find((d) => d.code === code)?.sample ?? null,
				initial: initialData,
				brandLogo,
				readOnly
			});
			if (!alive) {
				ed.destroy();
				return;
			}
			editor = ed;
			// Dev-only handle for headless verification / debugging in the browser console.
			(window as unknown as Record<string, unknown>).__gjs = ed;
			// Undo/redo integration for header/footer: the $effect above mirrors the schema
			// header/footer arrays onto the wrapper's `hfState` attribute (which the UndoManager
			// tracks). On undo/redo that attribute reverts — reflect it back into the schema so
			// the editor + canvas overlays follow. The signature guard prevents a feedback loop.
			const hfWrapper = ed.getWrapper?.();
			if (hfWrapper) {
				hfWrapper.on('change:hfState', () => {
					const sig = hfWrapper.get('hfState') as string | undefined;
					const cur = JSON.stringify({
						headers: [...schema.headers],
						footers: [...schema.footers]
					});
					if (!sig || sig === cur) return; // our own commit — schema already matches
					try {
						const parsed = JSON.parse(sig);
						schema.headers = parsed.headers ?? [];
						schema.footers = parsed.footers ?? [];
						applyHeaderFooter(ed, [...schema.headers], [...schema.footers]);
					} catch {
						/* malformed snapshot — ignore */
					}
				});
			}

			// Autosave: any editor change schedules a debounced save(). `scheduleAutosave` no-ops
			// until `autosaveReady`, which we arm only after the canvas has settled — so the initial
			// load's own change events don't persist (or roll a snapshot) before the user edits.
			ed.on('update', scheduleAutosave);
			setTimeout(() => {
				if (!alive) return;
				// Baseline the saved-content signature to the just-loaded state, so the first autosave only
				// fires if the user has genuinely changed something (not from load/settling updates).
				try {
					lastSavedSig = contentSig(buildSchema(ed, schema, layout));
				} catch {
					/* editor not ready — leave null, first real save baselines it */
				}
				autosaveReady = true;
			}, 800);

			// Mouse-wheel over the canvas must scroll the *canvas*, not the app shell. The real
			// scroll container is the iframe's own <html> (see pages.ts / scrollToPage), but GrapesJS's
			// overlay layers (highlighter/spot/tools) sit over the iframe in the PARENT document and
			// swallow the wheel before it reaches the iframe. The parent .gjs-cv-canvas has nothing
			// overflowing *it*, so the event bubbles up to the app shell's <html> and nudges its few
			// px of accidental overflow instead — the "scrollbar hangs near the end" feel. Intercept
			// it here: forward the delta to the iframe's scroller (normalising line/page delta modes,
			// and dividing by the CSS-zoom factor so a screen-pixel delta maps to the right number of
			// unscaled canvas pixels) and preventDefault so nothing escapes to the shell.
			onCanvasWheel = (e: WheelEvent) => {
				const doc = ed.Canvas?.getDocument?.() as Document | null;
				const scroller = (doc?.scrollingElement ?? doc?.documentElement) as HTMLElement | null;
				if (!scroller) return;
				if (scroller.scrollHeight - scroller.clientHeight <= 1) return; // nothing to scroll
				// Wheel delta may be in pixels (0), lines (1) or pages (2); normalise to pixels.
				const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? scroller.clientHeight : 1;
				const factor = (zoom || 100) / 100;
				scroller.scrollTop += (e.deltaY * unit) / factor;
				e.preventDefault();
			};
			canvasEl?.addEventListener('wheel', onCanvasWheel, { passive: false });

			// Apply the canvas layout overrides + fit ONLY once the canvas iframe is ready —
			// otherwise getDocument() is null and the injected <style> never lands.
			// Right-click anywhere in the canvas iframe → open our block/component menu.
			// Coordinates: the event's clientX/Y are in the iframe's own (unscaled) viewport,
			// so scale by the CSS-zoom factor and offset by the iframe's position in the parent.
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const onCtx = (e: any) => {
				e.preventDefault();
				const iframe = ed.Canvas?.getFrameEl?.() as HTMLIFrameElement | null;
				const rect = iframe?.getBoundingClientRect?.();
				const scale = zoom / 100;
				const x = (rect?.left ?? 0) + e.clientX * scale;
				const y = (rect?.top ?? 0) + e.clientY * scale;
				const comp = compFromEl(e.target);
				openContextMenu(x, y, comp);
			};
			const attachCtx = () => {
				if (ctxTeardown) return; // already wired for this frame
				const doc = ed.Canvas?.getDocument?.() as Document | null;
				if (!doc) return;
				doc.addEventListener('contextmenu', onCtx);
				ctxTeardown = () => doc.removeEventListener('contextmenu', onCtx);
			};

			// Page layout lives on the doc-page component props (pageSize/pageOrientation/
			// marginTop..marginLeft). It's a GLOBAL override (one .page{} rule for all pages),
			// so read it off the first page and drive applyPageLayout from the Svelte layer.
			const layoutFromFirstPage = (): PageLayout => {
				const p = getPageComponents(ed)[0];
				const d = defaultLayout();
				if (!p) return d;
				return {
					size: (p.get('pageSize') as PageSizeKey) ?? d.size,
					orientation: (p.get('pageOrientation') as Orientation) ?? d.orientation,
					margins: [
						Number(p.get('marginTop') ?? d.margins[0]),
						Number(p.get('marginRight') ?? d.margins[1]),
						Number(p.get('marginBottom') ?? d.margins[2]),
						Number(p.get('marginLeft') ?? d.margins[3])
					]
				};
			};
			// Re-apply geometry only — never re-fit. fitToWidth() would reset the user's zoom and
			// scroll on every tweak, and it can't centre a landscape sheet anyway (CSS `zoom` only
			// scales the frame visually — it doesn't change the iframe's internal viewport). A wide
			// (landscape) sheet is centred purely in the canvas CSS — see grapesjs-canvas.css
			// (html text-align:center + inline-block body) — so the zoom is left untouched.
			const syncLayoutFromPage = () => {
				layout = layoutFromFirstPage();
				applyPageLayout(ed);
			};

			const applyCanvas = () => {
				if (!alive) return;
				layout = layoutFromFirstPage();
				applyPageLayout(ed);
				fitToWidth();
				sendVarsToCanvas();
				attachCtx();
			};
			ed.on('load', applyCanvas);
			setTimeout(applyCanvas, 500); // fallback in case 'load' already fired

			// Track selection — always switch to Component tab and show traits/styles.
			// compAttrs is only populated for chart/table elements (data-comp-type).
			// Point the panels at a component (the primary of the current selection).
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const pointPanelsAt = (c: any) => {
				const attrs = c?.getAttributes?.() ?? {};
				selectedComp = c;
				jinjaVarKey = 'data-jinja-key' in attrs ? (attrs['data-jinja-key'] ?? '') : null;
				// A chart-frame's config lives on the locked chart inside it — surface that so
				// "Configure" edits the chart while the frame stays the selected/stylable unit.
				let cfg = c;
				if (c?.get?.('type') === 'chart-frame') {
					let found: { get?: (key: string) => unknown } | null = null;
					c.components?.()?.each?.((k: { get?: (key: string) => unknown }) => {
						if (!found && k?.get?.('type') === 'chart') found = k;
					});
					if (found) cfg = found;
				}
				const cfgAttrs = cfg?.getAttributes?.() ?? {};
				chartComp = cfgAttrs['data-comp-type'] ? cfg : null;
				compAttrs = cfgAttrs['data-comp-type'] ? { ...cfgAttrs } : {};
			};
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const syncSelectionList = (all: any[]) => {
				selectedCount = all.length;
				selectedNames = all.map((x) => x?.getName?.() ?? 'Element');
				// The CSS box edits only the primary element, so it's meaningless for a
				// multi-selection — drop back to the shared visual controls.
				if (all.length > 1) cssMode = false;
			};
			ed.on(
				'component:selected',
				(c: {
					getClasses?: () => string[];
					get?: (k: string) => unknown;
					parent?: () => { get?: (k: string) => unknown } | null | undefined;
				}) => {
					// A chart is wrapped in a chart-frame div; redirect selection to the frame so the whole
					// thing is one selectable unit. Padding/border/background work on the frame div (like a
					// column) — a bare chart can't be padded — so this is what the user styles.
					if (c?.get?.('type') === 'chart') {
						const frame = c.parent?.();
						if (frame?.get?.('type') === 'chart-frame') {
							ed.select(frame);
							return;
						}
					}
					pointPanelsAt(c);
					syncSelectionList(ed.getSelectedAll?.() ?? [c]);
					// Selecting a page points the per-page settings (background, border, header/footer)
					// at that page so the folded-in Page panel targets the right one.
					if (c?.getClasses?.().includes('page')) {
						const idx = getPageComponents(ed).indexOf(c);
						if (idx >= 0) activePage = idx;
					}
				}
			);
			// Double-click a chart/table on the canvas → select it + open the config modal. The
			// chart component view fires this custom event (see component-types.ts).
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			ed.on('chart:configure', (model: any) => {
				// Select the frame (the chart itself is locked/non-selectable); that points the panels
				// at this chart via pointPanelsAt. Seed the draft from the chart model directly.
				const parent = model?.parent?.();
				ed.select(parent?.get?.('type') === 'chart-frame' ? parent : model);
				chartDraft = chartDraftFrom((model?.get?.('attributes') as Record<string, string>) ?? {});
				seedManualFromDraft();
				chartConfigOpen = true;
			});
			ed.on('component:deselected', () => {
				// Multi-select: deselecting one may leave others — only clear when nothing remains,
				// otherwise keep the panels pointed at the primary of what's still selected.
				const all = ed.getSelectedAll?.() ?? [];
				syncSelectionList(all);
				if (all.length) {
					pointPanelsAt(ed.getSelected?.() ?? all[all.length - 1]);
				} else {
					selectedComp = null;
					compAttrs = {};
					jinjaVarKey = null;
				}
			});

			// Point the layer tree at the wrapper so all pages are visible at once.
			// The wrapper's own row is hidden via CSS (gjs-panels-theme-4) — only the
			// doc-page children are shown as top-level items.
			try {
				const wrapper = ed.getWrapper?.();
				if (wrapper) ed.LayerManager?.setRoot(wrapper);
			} catch {
				/* layer manager not ready */
			}

			// Keep the Pages rail labels in sync with the components' names (doc-page getName):
			// renames from the layer tree and page add/remove/reorder all funnel through here.
			const syncPageNames = () => {
				pageNames = getPageComponents(ed).map((c) => c.getName?.() ?? '');
			};
			syncPageNames();
			ed.on('component:add component:remove component:update:custom-name', syncPageNames);
			// Drive the sidebar table preview: any component edit (cell text on RTE commit, structure,
			// design) bumps the tick so the preview derived re-reads the live HTML.
			ed.on('component:update component:add component:remove', () => editorTick++);
			// Re-read the style rail. `style:target` and `style:custom` are the StyleManager's OWN
			// events and are the load-bearing pair: GrapesJS resolves the style target on a DEBOUNCED
			// handler, so on `component:selected` the models still answer for the PREVIOUS selection —
			// and a write then lands on the wrong element. The other four are the directions a value
			// can change from without the panel touching it.
			for (const ev of [
				'style:target',
				'style:custom',
				'undo',
				'redo',
				'component:styleUpdate',
				'rte:disable'
			])
				ed.on(ev, () => styleRevision++);
			// Track RTE edit mode so the live lint pauses while the caret is in a text box (see
			// rteActive). On exit, bump the tick so the lint recomputes from the just-committed text.
			ed.on('rte:enable', () => (rteActive = true));
			ed.on('rte:disable', () => {
				rteActive = false;
				editorTick++;
			});
			// Any page-geometry change re-applies layout but NEVER re-fits: fitToWidth would reset
			// the user's zoom/scroll. Centring (including of a wide/landscape sheet) is handled
			// purely in the canvas CSS (grapesjs-canvas.css / applyPageLayout), so no zoom change
			// is needed.
			ed.on(
				'component:update:pageSize component:update:pageOrientation component:update:marginTop component:update:marginRight component:update:marginBottom component:update:marginLeft',
				syncLayoutFromPage
			);

			stopWatch = watchActivePage(ed, (index, total) => {
				activePage = index;
				pageCount = total;
				syncPageNames();
			});

			onMessage = (e: MessageEvent) => {
				// Size/margin/padding dragged via the canvas box-model overlay (grapesjs-box-model.js).
				// A drag fires one message per mousemove; applying each with full tracking would push a
				// separate undo step per pixel and swamp the stack. Instead we pause the UndoManager for
				// the duration of the gesture (intermediate moves apply live but untracked), then on the
				// final 'end' message re-apply from the pre-drag value with tracking on, so the whole drag
				// collapses into exactly one undo step.
				if (e.data?.type === 'gjs-box-model') {
					const comp = ed.getSelected?.();
					const prop = e.data.prop as string | undefined;
					const value = e.data.value as string | undefined;
					if (!comp || !prop || !value) return;
					// On a page the padding tabs stand in for the page margin — translate a
					// padding-* drag (px) into the matching margin trait (mm) so it flows through
					// applyPageLayout instead of fighting its !important inline padding.
					const isPageComp = (comp.getClasses?.() ?? []).includes('page');
					if (isPageComp && prop.startsWith('padding-')) {
						const side = prop.slice('padding-'.length);
						const traitName = 'margin' + side.charAt(0).toUpperCase() + side.slice(1);
						const mm = Math.max(0, Math.round((parseFloat(value) / 3.779527559) * 10) / 10);
						if (e.data.phase === 'end') {
							if (boxDrag && boxDrag.prop === traitName && boxDrag.comp === comp) {
								comp.set(traitName, boxDrag.before);
								ed.UndoManager?.start?.();
								comp.set(traitName, mm);
							} else {
								comp.set(traitName, mm);
							}
							boxDrag = null;
						} else {
							if (!boxDrag || boxDrag.prop !== traitName || boxDrag.comp !== comp) {
								boxDrag = { comp, prop: traitName, before: comp.get(traitName) };
							}
							ed.UndoManager?.stop?.();
							comp.set(traitName, mm);
						}
						return;
					}
					if (e.data.phase === 'end') {
						if (boxDrag && boxDrag.prop === prop && boxDrag.comp === comp) {
							// Revert (untracked) to the pre-drag value, resume tracking, then apply the final
							// value so the UndoManager records a single before→after change.
							comp.addStyle({ [prop]: boxDrag.before }, { avoidStore: true });
							ed.UndoManager?.start?.();
							comp.addStyle({ [prop]: value });
						} else {
							comp.addStyle({ [prop]: value });
						}
						boxDrag = null;
					} else {
						// 'move' (or a legacy message with no phase): snapshot the pre-drag value once at the
						// start of the gesture, then keep undo tracking paused while the drag is live.
						if (!boxDrag || boxDrag.prop !== prop || boxDrag.comp !== comp) {
							boxDrag = { comp, prop, before: (comp.getStyle?.() ?? {})[prop] ?? '' };
						}
						ed.UndoManager?.stop?.();
						comp.addStyle({ [prop]: value }, { avoidStore: true });
					}
					return;
				}
				if (e.data?.type === 'gjs-add-page') {
					addPage(ed, typeof e.data.afterIndex === 'number' ? e.data.afterIndex : -1);
					return;
				}
				// A jinja-var chip was clicked in the canvas — show Component tab with variable picker.
				// Works for both toolbar-inserted and manually typed {{ var }} chips.
				if (e.data?.type === 'gjs-jinja-click') {
					jinjaVarKey = e.data.key ?? null;
					selectedComp = null;
				}
				// The "Insert field" picker asked the canvas to drop a token at the caret; if the caret was
				// gone (picker stole focus / RTE re-rendered the text), append at the component level so the
				// chosen field still lands somewhere sensible.
				if (e.data?.type === 'gjs-jinja-inserted') {
					if (!e.data.ok && typeof e.data.value === 'string') appendFieldFallback(e.data.value);
				}
				// Canvas reported which pages overflow one sheet → drive the in-editor overflow indicator.
				if (e.data?.type === 'gjs-page-overflow' && Array.isArray(e.data.pages)) {
					overflowPages = e.data.pages
						.filter((p: { overflow?: boolean }) => p?.overflow)
						.map((p: { index: number }) => p.index);
				}
			};
			window.addEventListener('message', onMessage);
		})().catch((err) => {
			// An unhandled rejection here is invisible to the reader: the promise is never awaited, so
			// the editor simply never appears. Name it on screen AND in the console — the message is
			// the difference between "the template is empty" and "the editor failed to start", which
			// look identical and lead to completely different investigations.
			console.error('[TemplateEditor] editor failed to initialise', err);
			initError =
				err instanceof Error && err.message
					? `The editor failed to start: ${err.message}`
					: 'The editor failed to start.';
		});

		return () => {
			alive = false;
			if (autosaveTimer) clearTimeout(autosaveTimer);
			if (stopWatch) stopWatch();
			if (onMessage) window.removeEventListener('message', onMessage);
			if (onCanvasWheel && canvasEl) canvasEl.removeEventListener('wheel', onCanvasWheel);
			if (ctxTeardown) ctxTeardown();
			if (editor) {
				editor.destroy();
				editor = null;
			}
		};
	});

	// Reapply the page layout to the canvas whenever it changes (load / undo / redo set
	// `layout`; geometry itself is read per-page from each doc-page's own traits).
	$effect(() => {
		void layout;
		if (editor) applyPageLayout(editor);
	});

	// Re-render per-page header/footer overlays whenever the arrays change, and mirror the
	// header/footer state onto a wrapper attribute so the GrapesJS UndoManager records edits
	// (schema is Svelte state it can't see otherwise). The wrapper listener below reverses the
	// flow on undo/redo. A signature comparison keeps the two in sync without a feedback loop.
	$effect(() => {
		if (!editor) return;
		const headers = [...schema.headers];
		const footers = [...schema.footers];
		applyHeaderFooter(editor, headers, footers);
		const wrapper = editor.getWrapper?.();
		if (!wrapper) return;
		const sig = JSON.stringify({ headers, footers });
		if (wrapper.get('hfState') !== sig) wrapper.set('hfState', sig);
	});

	// Set when GrapesJS never came up. Rendered over the canvas, because a blank canvas is exactly
	// what this failure looks like and the reader has no other way to tell the two apart.
	let initError = $state<string | null>(null);

	let saving = $state(false);
	// The in-flight persist, so a leave-guard flush can wait for it and then save the LATEST content
	// rather than no-op past the `saving` guard (which silently dropped an edit made mid-save).
	let inflightSave: Promise<void> | null = null;
	// Signature of the last content we persisted (or the just-loaded content). A save whose content
	// matches it is skipped — so opening a template, a programmatic/settling `update`, or an autosave
	// with no real change never overwrites the current version (or rolls a new 30-min snapshot) "for no
	// reason". Genuine edits change the signature and save normally.
	let lastSavedSig: string | null = null;
	function contentSig(built: { html: string | null; css: string }): string {
		return JSON.stringify({ h: built.html, c: built.css, p: editor?.getProjectData?.() ?? null });
	}
	async function save(): Promise<void> {
		if (readOnly || !editor || saving) return;
		// Build the render-ready export (Jinja HTML + CSS + page size) so the server can generate a
		// PDF from the saved version, and persist the full editor state to localStorage too.
		const built = buildSchema(editor, schema, layout);
		// Nothing actually changed since the last persist → don't save (avoids a needless overwrite /
		// snapshot roll). Still clears `dirty` so the leave-guard + status settle.
		const sig = contentSig(built);
		// Snapshot the edit counter: if it moves while this save is in flight, a newer edit landed and
		// `dirty` must NOT be cleared on success (else the leave-guard wouldn't fire for it).
		const seqAtStart = editSeq;
		if (sig === lastSavedSig) {
			dirty = false;
			return;
		}
		saveStored();
		const blob: PersistBlob = {
			gjs_project: editor.getProjectData?.(),
			layout,
			schema,
			linkedDatasets,
			html: built.html,
			css: built.css,
			pageSize: built.pageSize,
			components: built.components,
			datasets: built.datasets
		};
		// Persist to the real API (the page wires this to POST /templates/{uid}/versions). The
		// version history shown in the panel comes from the server, not a local log.
		saving = true;
		inflightSave = (async () => {
			try {
				await onSave?.(blob);
				lastSavedSig = sig;
				if (editSeq === seqAtStart) dirty = false; // clear only if no edit landed mid-save
				const now = new Date();
				savedAt = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
			} catch {
				toast.error('Save failed — your latest changes are not stored.');
			} finally {
				saving = false;
			}
		})();
		await inflightSave;
	}

	// Persist the LATEST content before leaving, even mid-autosave: wait out any in-flight save, then
	// save once more (a no-op if nothing changed since). Calling save() directly no-ops while a prior
	// save is in flight (the `saving` guard) — which used to drop an edit made during that window.
	async function flushSave(): Promise<void> {
		while (saving && inflightSave) await inflightSave;
		await save();
	}

	// Autosave: any editor change schedules a debounced save() (which persists via onSave + caches to
	// localStorage). `autosaveReady` gates out the flurry of change events during the initial load so
	// we don't persist — or roll a snapshot version — before the user has actually edited anything.
	let autosaveReady = false;
	let autosaveTimer: ReturnType<typeof setTimeout> | null = null;
	// `dirty` = an edit not yet persisted. Set the instant a change lands (so the leave-guard below
	// catches even an edit made within the autosave debounce); cleared once save() succeeds.
	let dirty = false;
	// Monotonic edit counter — save() snapshots it to tell whether a newer edit landed while it ran.
	let editSeq = 0;
	let leavingEditor = false;
	function scheduleAutosave(): void {
		if (readOnly || !autosaveReady) return;
		dirty = true;
		editSeq++;
		if (autosaveTimer) clearTimeout(autosaveTimer);
		autosaveTimer = setTimeout(() => void save(), 1200);
	}

	// Flush the pending autosave before leaving. This lives in the editor — not the page — because
	// save() (which builds the blob) is here, so it captures an edit the page's persist buffer
	// hasn't seen yet. In-app nav: cancel, flush, then continue. Browser unload: prompt.
	untrack(() => registerBeforeNavigate)?.((nav) => {
		if (leavingEditor || readOnly || !dirty || !nav.to) return;
		nav.cancel();
		if (autosaveTimer) clearTimeout(autosaveTimer);
		leavingEditor = true;
		const dest = nav.to.url.href;
		void flushSave().finally(() => navigate(dest));
	});
	$effect(() => {
		const onBeforeUnload = (e: BeforeUnloadEvent) => {
			if (dirty && !readOnly) {
				e.preventDefault();
				e.returnValue = '';
			}
		};
		window.addEventListener('beforeunload', onBeforeUnload);
		return () => window.removeEventListener('beforeunload', onBeforeUnload);
	});

	// Linking/unlinking datasets is Svelte state, not a GrapesJS model change, so autosave it too
	// (no-ops until ready, like every other autosave trigger).
	$effect(() => {
		void linkedDatasets;
		scheduleAutosave();
	});

	// After loadProjectData swaps the whole document, the canvas needs the same geometry pass the
	// initial load runs — re-apply the page layout and re-fit to width — once GrapesJS has
	// re-rendered the iframe. Without this the previewed version renders at the wrong page width.
	function resyncCanvasAfterLoad(nextLayout: PageLayout): void {
		setTimeout(() => {
			if (!editor) return;
			layout = nextLayout; // panel mirror; geometry itself is read per-page by applyPageLayout
			applyPageLayout(editor);
			fitToWidth();
		}, 400);
	}

	// Load a version's saved blob (fetched on demand) into the editor and diff it against the
	// working state. `switching` keeps the original priorBlob so Cancel always restores it.
	async function loadVersionIntoEditor(version: EditorVersion, switching: boolean): Promise<void> {
		if (!editor || !onLoadVersion) return;
		const priorBlob: PersistBlob =
			switching && versionPreview
				? versionPreview.priorBlob
				: { gjs_project: editor.getProjectData?.(), layout, schema, linkedDatasets };
		const blob = (await onLoadVersion(version.uid)) as PersistBlob | null;
		if (!blob) return;
		versionPreview = { version, priorBlob };
		editor.loadProjectData(blob.gjs_project);
		schema = parseSchema(blob.schema);
		linkedDatasets = blob.linkedDatasets;
		historyOpen = false;
		resyncCanvasAfterLoad(blob.layout);
		scheduleDiffInject(priorBlob.gjs_project, blob.gjs_project);
	}

	function previewVersion(version: EditorVersion): void {
		void loadVersionIntoEditor(version, false);
	}

	function switchVersion(version: EditorVersion): void {
		void loadVersionIntoEditor(version, true);
	}

	function restoreVersion(): void {
		if (!versionPreview) return;
		clearDiffHighlights(editor);
		versionPreview = null;
		diffStats = null;
		// Current editor state is already the historical version — save it as a fresh draft.
		void save();
	}

	function cancelVersionPreview(): void {
		if (!versionPreview || !editor) return;
		const { priorBlob } = versionPreview;
		clearDiffHighlights(editor);
		editor.loadProjectData(priorBlob.gjs_project);
		schema = parseSchema(priorBlob.schema);
		linkedDatasets = priorBlob.linkedDatasets;
		versionPreview = null;
		diffStats = null;
		resyncCanvasAfterLoad(priorBlob.layout);
	}

	const versionIndex = $derived(
		versionPreview ? historyEntries.findIndex((v) => v.uid === versionPreview!.version.uid) : -1
	);

	const undo = () => editor?.UndoManager?.undo();
	const redo = () => editor?.UndoManager?.redo();

	// Undo-history dropdown (undo only). GrapesJS fuses related changes (e.g. a whole
	// drag) into one step via getGroupedStack(); getPointer() marks the last applied
	// raw action, so groups with index <= pointer are the ones that can still be undone.
	let undoOpen = $state(false);
	let undoHover = $state(-1);
	let undoList = $state<{ label: string }[]>([]);

	type UndoAction = { type?: string; after?: Record<string, unknown> };
	type UndoGroupLite = { index: number; labels?: string[]; actions: UndoAction[] };

	function labelForGroup(g: UndoGroupLite): string {
		if (g.labels?.length) return g.labels.join(', ');
		const actions = g.actions ?? [];
		if (actions.some((a) => a.type === 'add')) return 'Add element';
		if (actions.some((a) => a.type === 'remove')) return 'Remove element';
		const after = actions[0]?.after ?? {};
		if ('style' in after) return 'Style change';
		if ('content' in after) return 'Text edit';
		if ('attributes' in after || 'traits' in after) return 'Attribute change';
		return 'Edit';
	}

	function buildUndoList(): { label: string }[] {
		const um = editor?.UndoManager;
		if (!um) return [];
		const pointer = um.getPointer();
		// Applied groups (index <= pointer) are undoable; show them newest-first.
		const groups = um.getGroupedStack() as UndoGroupLite[];
		return groups
			.filter((g) => g.index <= pointer)
			.reverse()
			.map((g) => ({ label: labelForGroup(g) }));
	}

	function openUndoMenu() {
		undoList = buildUndoList();
		undoHover = -1;
	}

	// Undo down to and including the chosen entry (newest = index 0).
	function undoTo(i: number) {
		const um = editor?.UndoManager;
		if (!um) return;
		for (let n = 0; n <= i; n++) um.undo();
		undoOpen = false;
	}

	const newPage = () => editor && addPage(editor, -1);
	function goToPage(index: number): void {
		if (!editor || index < 0 || index >= pageCount) return;
		activePage = index; // optimistic — the scroll watcher confirms it
		scrollToPage(editor, index);
	}
	function deletePage(index: number): void {
		if (!editor || pageCount <= 1) return;
		const comp = getPageComponents(editor)[index];
		comp?.remove?.();
	}
	function copyPage(index: number): void {
		if (!editor) return;
		duplicatePage(editor, index);
		goToPage(index + 1);
	}

	// Copy the SELECTED page's PAGE SETUP — paper size / orientation / margins (component traits)
	// plus background & border (inline style) — onto every other page. Each page can still be
	// changed individually afterwards.
	function applyPageSetupToAll(): void {
		if (!editor) return;
		const pages = getPageComponents(editor);
		const srcIndex = selectedPageIndex;
		const src = pages[srcIndex];
		if (!src) return;
		const geom = {
			pageSize: src.get('pageSize'),
			pageOrientation: src.get('pageOrientation'),
			marginTop: src.get('marginTop'),
			marginRight: src.get('marginRight'),
			marginBottom: src.get('marginBottom'),
			marginLeft: src.get('marginLeft')
		};
		const srcStyle = getPageStyle(editor, srcIndex);
		pages.forEach((p, i) => {
			if (i === srcIndex) return;
			p.set(geom);
			setPageStyle(editor, i, {
				background: srcStyle.background ?? '',
				border: srcStyle.border ?? '',
				'border-radius': srcStyle['border-radius'] ?? ''
			});
		});
		applyPageLayout(editor);
	}

	// Copy the SELECTED page's HEADER & FOOTER onto every other page. An enabled header/footer is
	// cloned; if the source has none, a disabled entry is written so targets lose theirs too.
	function applyHeaderFooterToAll(): void {
		if (!editor) return;
		const pages = getPageComponents(editor);
		const srcIndex = selectedPageIndex;
		if (!pages[srcIndex]) return;
		const srcHeader = schema.headers[srcIndex];
		const srcFooter = schema.footers[srcIndex];
		pages.forEach((_, i) => {
			if (i === srcIndex) return;
			schema.headers[i] = srcHeader?.enabled
				? { ...srcHeader }
				: { ...defaultHeader(), enabled: false };
			schema.footers[i] = srcFooter?.enabled
				? { ...srcFooter }
				: { ...defaultFooter(), enabled: false };
		});
		applyHeaderFooter(editor, [...schema.headers], [...schema.footers]);
	}

	function onDragStart(e: DragEvent, index: number): void {
		dragFromIndex = index;
		if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
	}
	function onDragOver(e: DragEvent, index: number): void {
		e.preventDefault();
		if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
		dragOverIndex = index;
	}
	function onDrop(e: DragEvent, index: number): void {
		e.preventDefault();
		if (dragFromIndex !== null && dragFromIndex !== index) {
			movePage(editor, dragFromIndex, index);
			goToPage(index);
		}
		dragFromIndex = null;
		dragOverIndex = null;
	}
	function onDragEnd(): void {
		dragFromIndex = null;
		dragOverIndex = null;
	}

	// ── Canvas context menu ───────────────────────────────────────────────────
	/* eslint-disable @typescript-eslint/no-explicit-any */

	/** Map a DOM node inside the canvas iframe to its owning GrapesJS component by
	    walking up the DOM and matching each ancestor against the component tree. */
	function compFromEl(el: any): any {
		if (!editor || !el) return null;
		const wrapper = editor.getWrapper?.();
		if (!wrapper) return null;
		const findByEl = (node: any): any => {
			let out: any = null;
			const walk = (comp: any) => {
				if (out) return;
				if (comp.getEl?.() === node) {
					out = comp;
					return;
				}
				comp.components?.()?.each?.((c: any) => walk(c));
			};
			walk(wrapper);
			return out;
		};
		let node: any = el;
		const bodyEl = editor.Canvas?.getBody?.();
		while (node && node !== bodyEl) {
			const hit = findByEl(node);
			if (hit) return hit;
			node = node.parentElement;
		}
		return null;
	}

	/** Where to drop an inserted block, given the right-clicked component. */
	function resolveInsertTarget(comp: any): { parent: any; at?: number } {
		const wrapper = editor?.getWrapper?.();
		const pages = getPageComponents(editor);
		const activePageComp = pages[activePage] ?? pages[0];
		if (!comp || comp === wrapper) return { parent: activePageComp };
		const classes: string[] = comp.getClasses?.() ?? [];
		// Pages and layout cells are containers — drop inside them.
		if (classes.includes('page') || classes.includes('gjs-cell') || classes.includes('doc-col')) {
			return { parent: comp };
		}
		// Otherwise insert right after the target within its parent.
		const p = comp.parent?.();
		if (!p) return { parent: activePageComp };
		return { parent: p, at: (comp.index?.() ?? -1) + 1 };
	}

	function buildBlockGroups(): void {
		const all = editor?.BlockManager?.getAll?.();
		if (!all) {
			ctxBlockGroups = [];
			return;
		}
		const catLabel = (cat: any): string => {
			if (!cat) return 'Other';
			if (typeof cat === 'string') return cat;
			if (typeof cat.get === 'function') return cat.get('label') || cat.get('id') || 'Other';
			return cat.label || cat.id || 'Other';
		};
		const groups = new Map<string, { id: string; label: string; media: string }[]>();
		all.forEach((b: any) => {
			const label = catLabel(b.get?.('category'));
			if (!groups.has(label)) groups.set(label, []);
			groups.get(label)!.push({
				id: b.getId?.() ?? b.get?.('id'),
				label: b.get?.('label') ?? '',
				media: typeof b.get?.('media') === 'string' ? b.get('media') : ''
			});
		});
		ctxBlockGroups = [...groups.entries()].map(([label, blocks]) => ({ label, blocks }));
	}

	function openContextMenu(x: number, y: number, comp: any): void {
		if (readOnly) return; // no insert/duplicate/delete for a view-only principal
		buildBlockGroups();
		ctxMenu = { x, y, comp };
	}
	function closeContextMenu(): void {
		ctxMenu = null;
	}

	// Reposition once mounted so the menu never overflows the viewport edges.
	$effect(() => {
		if (!ctxMenu || !ctxMenuEl) return;
		const r = ctxMenuEl.getBoundingClientRect();
		let nx = ctxMenu.x;
		let ny = ctxMenu.y;
		if (nx + r.width > window.innerWidth - 8) nx = window.innerWidth - 8 - r.width;
		if (ny + r.height > window.innerHeight - 8) ny = window.innerHeight - 8 - r.height;
		nx = Math.max(8, nx);
		ny = Math.max(8, ny);
		if (nx !== ctxMenu.x || ny !== ctxMenu.y) ctxMenu = { ...ctxMenu, x: nx, y: ny };
	});

	function insertBlockFromMenu(blockId: string): void {
		const block = editor?.BlockManager?.get?.(blockId);
		if (!block) {
			closeContextMenu();
			return;
		}
		const content = block.get('content');
		const { parent, at } = resolveInsertTarget(ctxMenu?.comp);
		try {
			const added = parent?.append?.(content, at != null ? { at } : {});
			const comp = Array.isArray(added) ? added[0] : added;
			if (comp) editor.select?.(comp);
		} catch {
			/* append failed — ignore */
		}
		closeContextMenu();
	}

	// ── Insert field (merge variable) ──────────────────────────────────────────
	// The toolbar catalogue picker. Selecting a field drops a plain {{ token }} — the same
	// representation the type-`{{` autocomplete produces — at the caret inside the text box being
	// edited, via the iframe bridge (grapesjs-jinja-vars.js). Opening the picker moves focus out of
	// the canvas, so the iframe keeps a live snapshot of the caret and restores it; if that snapshot
	// is stale (RTE left edit mode and re-rendered the text), the iframe replies ok:false and we
	// append the field at the component level instead — the button is never a dead end.
	let insertFieldOpen = $state(false);
	let insertFieldQuery = $state('');
	// The right-click "Insert variable" uses a modal Dialog (not the toolbar Popover): a Popover won't
	// open reliably from the context-menu teardown, whereas a Dialog opens cleanly via bind:open.
	let insertVarDialogOpen = $state(false);

	function insertField(key: string): void {
		insertFieldOpen = false;
		insertVarDialogOpen = false;
		insertFieldQuery = '';
		try {
			const iframe = editor?.Canvas?.getFrameEl?.() as HTMLIFrameElement | null;
			if (iframe?.contentWindow) {
				iframe.contentWindow.postMessage({ type: 'gjs-jinja-insert', value: key }, '*');
				return;
			}
		} catch {
			/* fall through to a component-level append */
		}
		appendFieldFallback(key);
	}

	/** Drop `{{ key }}` at the component level when there's no live caret: into the selected text box
	    if one is selected, otherwise as a NEW editable text component on the active page. The new node
	    is `data-gjs-type="text"` — the same accessible, RTE-editable component the Text block drops —
	    never a bare <p> (which falls to the base type and isn't editable). */
	function appendFieldFallback(key: string): void {
		if (!editor) return;
		const token = `{{ ${key} }}`;
		const sel = editor.getSelected?.();
		const editable = !!sel && (sel.get?.('type') === 'text' || sel.get?.('editable'));
		try {
			if (editable) {
				sel.append?.(token);
				editor.select?.(sel);
			} else {
				const { parent, at } = resolveInsertTarget(sel ?? undefined);
				const added = parent?.append?.(
					`<div data-gjs-type="text">${token}</div>`,
					at != null ? { at } : {}
				);
				const comp = Array.isArray(added) ? added[0] : added;
				if (comp) editor.select?.(comp);
			}
			editorTick++;
		} catch {
			/* append failed — ignore */
		}
	}

	function ctxDuplicate(): void {
		const comp = ctxMenu?.comp;
		if (editor && comp) {
			try {
				editor.select?.(comp);
				editor.runCommand?.('core:copy');
				editor.runCommand?.('core:paste');
			} catch {
				/* command unavailable */
			}
		}
		closeContextMenu();
	}

	// Right-click → "Insert variable": select the clicked element as the insert target, then open the
	// same variable picker the toolbar uses. On pick, insertField appends the token into the selected
	// element (or a new text block), so a variable can be added without hunting for the toolbar button.
	// Opening the picker is deferred to a fresh task: this handler fires from the context menu (opened
	// by a manual iframe listener), and toggling the Popover synchronously in that context doesn't
	// reliably flush.
	function ctxInsertVariable(): void {
		const comp = ctxMenu?.comp;
		if (comp) editor?.select?.(comp);
		closeContextMenu();
		// Deferred to a fresh task, then flushed synchronously: the click originates from the context
		// menu (opened by a manual iframe listener) whose state writes don't auto-flush, so force it.
		setTimeout(() => {
			insertVarDialogOpen = true;
			flushSync();
		}, 0);
	}

	function ctxDelete(): void {
		const comp = ctxMenu?.comp;
		if (comp && comp !== editor?.getWrapper?.()) {
			try {
				comp.remove?.();
			} catch {
				/* ignore */
			}
		}
		closeContextMenu();
	}

	/** True when the right-clicked target is a real, removable element (not the
	    page body / wrapper / a layout page div). */
	const ctxHasComp = $derived.by(() => {
		const comp = ctxMenu?.comp;
		if (!comp || comp === editor?.getWrapper?.()) return false;
		const classes: string[] = comp.getClasses?.() ?? [];
		return !classes.includes('page');
	});
	const ctxCompName = $derived(ctxMenu?.comp?.getName?.() ?? 'Element');

	/* eslint-enable @typescript-eslint/no-explicit-any */

	let previewMode = $state(false);
	// Server-rendered document preview (sample data) — the real render pipeline (Jinja + chart/table
	// substitution), shown in an iframe over the canvas, not the raw GrapesJS canvas.
	let docPreviewHtml = $state<string | null>(null);
	// The true-to-generation surface: a real PDF (same engine generation uses) as a blob object URL,
	// shown in the browser's native PDF viewer so pagination/overflow appear exactly as generated.
	// Preferred over the HTML preview when present; the HTML preview is the fallback (see togglePreview).
	let docPreviewPdfUrl = $state<string | null>(null);
	let docPreviewLoading = $state(false);
	let docPreviewError = $state<string | null>(null);

	// Free the previous PDF blob URL before replacing it or closing the preview (blobs live until the
	// document is unloaded otherwise).
	function revokePreviewPdf(): void {
		if (docPreviewPdfUrl) {
			URL.revokeObjectURL(docPreviewPdfUrl);
			docPreviewPdfUrl = null;
		}
	}
	// Variables the previewed template references that aren't in the catalogue (hand-typed / typos),
	// each with the closest catalogue key as a "did you mean" hint.
	let docPreviewUnknowns = $state<{ name: string; suggestion?: string | null }[]>([]);
	// The unknown-vars banner is dismissable within a preview view, but re-appears each time Preview
	// is (re)opened (reset in togglePreview) so the warning isn't permanently silenced.
	let previewWarningDismissed = $state(false);
	// The preview iframe is sized to its content (no inner scrollbar) and scaled by the shared `zoom`
	// state via CSS `zoom` on its wrapper — the outer container scrolls (mirrors platform-1). A4-ish
	// defaults until the first onload measures the real page.
	let previewFrameEl = $state<HTMLIFrameElement | null>(null);
	let previewFrameWidth = $state(820);
	let previewFrameHeight = $state(1160);

	// The last preview we rendered is CACHED (kept on screen even when Preview is toggled off), keyed by
	// a signature of the schema it was built from — so re-opening Preview after no edits is instant and
	// doesn't re-render, and regenerating shows a spinner OVER the previous preview instead of a blank
	// "Rendering…" flash.
	let lastPreviewSig: string | null = null;
	function schemaSig(built: unknown): string {
		const s = JSON.stringify(built);
		let h = 5381;
		for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
		return `${s.length}:${h}`;
	}

	async function togglePreview(): Promise<void> {
		previewMode = !previewMode;
		// Hide the editor-only jinja-token highlight while previewing.
		try {
			const frame = editor?.Canvas?.getFrameEl?.() as HTMLIFrameElement | null;
			frame?.contentWindow?.postMessage({ type: 'gjs-jinja-hl', on: !previewMode }, '*');
		} catch {
			/* iframe not ready */
		}
		if (!previewMode) {
			// Keep the rendered preview cached (don't clear/revoke) so re-opening is instant when nothing
			// changed. It's freed when replaced by a fresh render or when the editor is destroyed.
			setTimeout(fitToWidth, 80); // sidebars return, canvas resizes — refit the zoom
			return;
		}
		await renderDocPreview(false);
	}

	/** Render the current schema to the data-filled sample preview. Skips work when the schema hasn't
	    changed since the last render (cache hit). While rendering it leaves the previous preview visible
	    and shows a spinner over it (see docPreviewLoading in the markup). `force` re-renders regardless
	    (the Refresh button). */
	async function renderDocPreview(force: boolean): Promise<void> {
		if (!editor || !renderPreview) {
			docPreviewError = 'Preview is unavailable.';
			return;
		}
		const built = buildSchema(editor, schema, layout);
		const sig = schemaSig(built);
		// Cache hit — nothing changed since the last render. Reuse it (no flicker, no server round-trip).
		if (!force && sig === lastPreviewSig && (docPreviewPdfUrl || docPreviewHtml)) {
			setTimeout(fitToWidth, 80);
			return;
		}
		docPreviewLoading = true;
		docPreviewError = null;
		previewWarningDismissed = false; // re-show the warning on each fresh render
		// The HTML preview (fast, no WeasyPrint) drives the unknown-variable lint and is the fallback
		// surface; the PDF (true-to-generation) is the preferred surface. Render both concurrently so the
		// slow PDF doesn't hold up the lint, and a PDF failure (e.g. no native libs in local dev) still
		// leaves the HTML preview showing. NOTE: the previous preview stays on screen throughout (spinner
		// overlay) — we only swap it once the new render resolves.
		const [htmlRes, pdfRes] = await Promise.allSettled([
			renderPreview(built),
			renderPreviewPdf ? renderPreviewPdf(built) : Promise.reject(new Error('no pdf renderer'))
		]);
		if (htmlRes.status === 'fulfilled') {
			docPreviewHtml = htmlRes.value.html;
			docPreviewUnknowns = htmlRes.value.unknownVariables ?? [];
		}
		const pdfConfigured = !!renderPreviewPdf;
		if (pdfRes.status === 'fulfilled') {
			revokePreviewPdf(); // free the previous blob only now the new one is ready
			docPreviewPdfUrl = URL.createObjectURL(pdfRes.value);
		} else {
			// The PDF render failed for THIS schema — drop any previous PDF so a now-stale render isn't
			// presented as current; the (current) HTML preview below is the fallback surface.
			revokePreviewPdf();
		}
		// Error only when there's nothing to show at all. An empty template yields no HTML and a 422
		// (no PDF) — that's the "add some content" case, not a hard failure.
		if (!docPreviewPdfUrl && !docPreviewHtml) {
			docPreviewError =
				htmlRes.status === 'fulfilled'
					? 'Nothing to preview yet — add some content to the page.'
					: 'Could not render the preview.';
		} else if (pdfRes.status === 'fulfilled' || !pdfConfigured) {
			// Cache only a COMPLETE render (PDF shown) or an HTML-only one when no PDF engine is wired
			// (permanent). A configured-but-failed PDF (e.g. a transient worker outage) is left uncached
			// so re-opening retries it rather than serving the HTML fallback forever on a cache hit.
			lastPreviewSig = sig;
		}
		docPreviewLoading = false;
	}

	// Measure the rendered preview so the iframe is exactly its content tall (no inner scrollbar):
	// the outer container scrolls and the shared `zoom` scales the wrapper. srcdoc is same-origin, so
	// the content document is readable. Page height is fixed (A4) regardless of iframe width, so this
	// is stable. +48px width leaves room for the page's drop shadow within the grey surround.
	function sizePreviewFrame(): void {
		const doc = previewFrameEl?.contentDocument;
		if (!doc) return;
		const page = doc.querySelector('.page') as HTMLElement | null;
		if (page?.offsetWidth) previewFrameWidth = page.offsetWidth + 48;
		const h = Math.max(doc.body?.scrollHeight ?? 0, doc.documentElement?.scrollHeight ?? 0);
		if (h) previewFrameHeight = h;
	}

	// Free any live preview blob URL when the editor unmounts (the body reads nothing reactive, so it
	// runs once and only the teardown fires — on destroy).
	$effect(() => () => revokePreviewPdf());

	// Zoom
	$effect(() => {
		if (editor) applyZoom(editor, zoom);
	});
	const zoomIn = () => (zoom = Math.min(200, zoom + 10));
	const zoomOut = () => (zoom = Math.max(25, zoom - 10));
	const zoomReset = () => (zoom = 100);
	/** Zoom so the whole page width fits the canvas column (an A4 page is wider than the
	    canvas on a normal screen, so at 100% it would overflow/clip — mirrors the prototype). */
	function fitToWidth(): void {
		const cv = document.querySelector('.gjs-cv-canvas') as HTMLElement | null;
		if (!cv) return;
		const avail = cv.clientWidth - 40; // canvas padding
		if (avail < 120) return; // canvas not laid out yet — don't clamp to a silly value
		// Fit the WIDEST page (the body is sized to it) so a landscape sheet anywhere in the
		// document fits the canvas column and centres, rather than overflowing off-centre.
		let maxWmm = pageDimensions(layout).w;
		if (editor) {
			for (const c of getPageComponents(editor)) {
				const w = pageDimensions({
					size: (c.get('pageSize') as PageSizeKey) ?? 'A4',
					orientation: (c.get('pageOrientation') as Orientation) ?? 'portrait',
					margins: [0, 0, 0, 0]
				}).w;
				if (w > maxWmm) maxWmm = w;
			}
		}
		const pagePx = maxWmm * 3.7795; // mm → px @96dpi
		if (pagePx <= 0) return;
		zoom = Math.max(25, Math.min(100, Math.round((avail / pagePx) * 100)));
	}

	// Chart/table component configuration (writes the data-comp-* contract).
	const compType = $derived(compAttrs['data-comp-type'] ?? '');
	const isTable = $derived(compType === 'data_table');
	const compTypeLabel = $derived(isTable ? 'Table' : 'Chart');
	const compIsBound = $derived(
		!!compAttrs['data-comp-data-source'] || compAttrs['data-comp-source-mode'] === 'manual'
	);

	// Chart/table config modal
	let chartConfigOpen = $state(false);
	let chartDraft = $state<Record<string, string>>({});

	// Manual data-source mode (#7): a chart/table can carry its OWN rows instead of a dataset. The
	// editable grid binds these; an $effect below serialises them into chartDraft['data-comp-inline-
	// data'] so applyChartConfig persists them on the component (→ serialize.ts → binding._inline_rows).
	let manualColumns = $state<string[]>(['Column 1', 'Column 2']);
	let manualRows = $state<string[][]>([['', '']]);
	function seedManualFromDraft(): void {
		try {
			const d = JSON.parse(chartDraft['data-comp-inline-data'] || '{}');
			const cols = Array.isArray(d.columns) ? d.columns.map(String) : [];
			const rws = Array.isArray(d.rows)
				? d.rows.map((r: unknown) => (Array.isArray(r) ? r.map((c) => String(c ?? '')) : []))
				: [];
			manualColumns = cols.length ? cols : ['Column 1', 'Column 2'];
			manualRows = rws.length ? rws : [manualColumns.map(() => '')];
		} catch {
			manualColumns = ['Column 1', 'Column 2'];
			manualRows = [['', '']];
		}
	}

	// WHAT AN AUTHOR MAY PICK. `donut_chart` is deliberately absent and the RENDERERS still handle it
	// (`chart-preview.ts`, `charts.py`): a template already built on one must keep rendering exactly
	// as it did, and removing it from those would silently change a published document. Retiring a
	// type is a decision about what to offer, never about what to honour.
	const CHART_TYPES = [
		['line_chart', 'Line Chart'],
		['bar_chart', 'Bar Chart'],
		['pie_chart', 'Pie Chart'],
		['stacked_area_chart', 'Stacked Area']
	] as const;

	// Build the config draft from a component's data-comp-* attributes (shared by the panel button
	// and the double-click-to-configure handler, which reads the double-clicked model's attrs).
	function chartDraftFrom(a: Record<string, string>): Record<string, string> {
		return {
			'data-comp-type': a['data-comp-type'] ?? 'line_chart',
			'data-comp-data-source': a['data-comp-data-source'] ?? '',
			'data-comp-source-mode': a['data-comp-source-mode'] ?? 'dataset',
			'data-comp-inline-data': a['data-comp-inline-data'] ?? '',
			'data-comp-x-field': a['data-comp-x-field'] ?? '',
			'data-comp-y-field': a['data-comp-y-field'] ?? '',
			'data-comp-label-field': a['data-comp-label-field'] ?? '',
			'data-comp-value-field': a['data-comp-value-field'] ?? '',
			'data-comp-title': a['data-comp-title'] ?? '',
			'data-comp-x-label': a['data-comp-x-label'] ?? '',
			'data-comp-y-label': a['data-comp-y-label'] ?? '',
			'data-comp-columns': a['data-comp-columns'] ?? '',
			'data-comp-headers': a['data-comp-headers'] ?? '',
			'data-comp-header-bg': a['data-comp-header-bg'] ?? '#1e3a5f',
			'data-comp-header-fg': a['data-comp-header-fg'] ?? '#ffffff',
			'data-comp-striped': a['data-comp-striped'] ?? 'true',
			'data-comp-show-grid': a['data-comp-show-grid'] ?? 'true',
			'data-comp-show-legend': a['data-comp-show-legend'] ?? 'true',
			// Per-column width (a percentage) and alignment, one entry per column, blank for automatic.
			'data-comp-widths': a['data-comp-widths'] ?? '',
			'data-comp-aligns': a['data-comp-aligns'] ?? '',
			'data-comp-value-format': a['data-comp-value-format'] ?? 'number',
			// Blank means AUTOMATIC — an integer prints bare, anything else to at most 2dp. It cannot
			// default to '0': that is a real answer (a chart of whole pounds) and would silently round
			// every fractional figure on every existing chart.
			'data-comp-value-decimals': a['data-comp-value-decimals'] ?? '',
			'data-comp-show-values': a['data-comp-show-values'] ?? 'false',
			'data-comp-line-width': a['data-comp-line-width'] ?? '1.5',
			// TYPOGRAPHY. Blank means "whatever the renderer's default is" for all three, which is what
			// keeps an already-authored chart drawing exactly as it did: `charts.py::_typography` falls
			// back per field, so an absent value is never written as a chosen one.
			'data-comp-font-family': a['data-comp-font-family'] ?? '',
			'data-comp-font-size': a['data-comp-font-size'] ?? '',
			'data-comp-text-color': a['data-comp-text-color'] ?? '',
			// The chart's own background. Blank ⇒ transparent, which is how every chart has drawn, so
			// one already carrying a background on its FRAME is untouched by the move.
			'data-comp-background': a['data-comp-background'] ?? '',
			// The gridlines, and the axis with them. Blank ⇒ the default pair (`charts.py::_grid_ink`).
			'data-comp-grid-color': a['data-comp-grid-color'] ?? '',
			// PER-TYPE SHAPE. Blank on every one of them, and blank means what the chart has always
			// drawn — a named step falls back to the first entry and a switch falls back to off, so a
			// component authored before these existed gains no ink from them shipping.
			'data-comp-line-style': a['data-comp-line-style'] ?? '',
			'data-comp-show-points': a['data-comp-show-points'] ?? '',
			'data-comp-area-fill': a['data-comp-area-fill'] ?? '',
			'data-comp-bar-width': a['data-comp-bar-width'] ?? '',
			'data-comp-bar-corners': a['data-comp-bar-corners'] ?? '',
			'data-comp-ring-width': a['data-comp-ring-width'] ?? '',
			// THE TENANT'S BRANDING IS THE DEFAULT, and seeding it here is what makes that true of the
			// OUTPUT and not just of this preview. The worker knows nothing of a firm's CSS variables,
			// so a colour that is only a fallback in the editor is a colour the PDF never uses — which
			// is exactly how the preview came to disagree with the document. Written onto the component
			// instead, it is a real choice: the firm's own palette by default, and alterable, and the
			// same in the drawer, on the page and in the PDF.
			'data-comp-colors': a['data-comp-colors'] || tenantPalette.join(',')
		};
	}

	// Which tab of the config drawer is showing. Reset on every open: the drawer is reached by
	// double-clicking a component, and landing on Appearance because that is where you were last time
	// hides the bindings you came to change.
	let configTab = $state<'data' | 'appearance'>('data');

	function openChartConfig(): void {
		chartDraft = chartDraftFrom(compAttrs);
		seedManualFromDraft();
		configTab = 'data';
		chartConfigOpen = true;
	}

	function applyChartConfig(): void {
		// Apply every drafted attribute in ONE addAttributes call so the whole "Apply" is a single
		// undo step — not ~19 (one per data-comp-* key applied separately). compAttrs mirrors them
		// for the reactive panel. Writes to the chart model (chartComp) — which, for a chart-frame
		// selection, is the locked chart inside it, not the selected frame.
		if (chartComp) {
			compAttrs = { ...compAttrs, ...chartDraft };
			chartComp.addAttributes({ ...chartDraft });
		}
		chartConfigOpen = false;
	}

	// Switch the chart/table data source between Dataset and Manual. The two are mutually exclusive —
	// clear the OTHER side and reset the field bindings, because dataset columns and manual columns
	// differ: a shared x/y/columns selection would resolve against the wrong side at generate and
	// render blank (the bug this fixes). Manual data is keyed off `data-comp-inline-data` presence
	// downstream (serialize.ts / component-types.ts), so clearing it on switch-to-dataset is what
	// actually turns manual off — independent of whether this UI flag survives a round-trip.
	// ── per-column layout for a data table ──────────────────────────────────────────────────────
	// Stored as one comma-separated entry per column, positionally, so a column added or removed
	// keeps the others aligned with it — the same shape `data-comp-columns`/`-headers` already use.
	// Blank means AUTOMATIC, which is why an empty string is a real value here and not an absence.
	function columnPart(key: 'widths' | 'aligns', index: number): string {
		return (chartDraft[`data-comp-${key}`] ?? '').split(',')[index]?.trim() ?? '';
	}

	function setColumnPart(key: 'widths' | 'aligns', index: number, value: string): void {
		const attr = `data-comp-${key}`;
		const parts = (chartDraft[attr] ?? '').split(',').map((p) => p.trim());
		while (parts.length <= index) parts.push('');
		parts[index] = value;
		// Trailing blanks carry no information and would grow the attribute every time a column is
		// touched, so the list is trimmed back to its last real entry.
		while (parts.length && parts[parts.length - 1] === '') parts.pop();
		chartDraft[attr] = parts.join(',');
	}

	function setChartSourceMode(mode: string): void {
		if ((chartDraft['data-comp-source-mode'] ?? 'dataset') === mode) return;
		chartDraft['data-comp-source-mode'] = mode;
		if (mode === 'manual') chartDraft['data-comp-data-source'] = '';
		else chartDraft['data-comp-inline-data'] = '';
		chartDraft['data-comp-x-field'] = '';
		chartDraft['data-comp-y-field'] = '';
		chartDraft['data-comp-label-field'] = '';
		chartDraft['data-comp-value-field'] = '';
		chartDraft['data-comp-columns'] = '';
	}

	const draftType = $derived(chartDraft['data-comp-type'] ?? 'line_chart');
	const draftIsTable = $derived(draftType === 'data_table');
	const draftIsPie = $derived(draftType === 'pie_chart' || draftType === 'donut_chart');

	/** The chart's series palette as an editable LIST. The component carries it as one comma-separated
	 *  string (`data-comp-colors`), which is what both renderers read; this is that value split so each
	 *  entry can have its own picker. */
	const draftPalette = $derived(
		(chartDraft['data-comp-colors'] ?? '')
			.split(',')
			.map((c) => c.trim())
			.filter(Boolean)
	);

	/** What each colour in the palette actually paints, in the renderer's own order — series N takes
	 *  `palette[N % len]`. A pie has no bound series: its palette runs across SLICES, and how many
	 *  there are is only known once the dataset resolves, so those are numbered rather than named. */
	const draftColourSlots = $derived.by(() => {
		if (draftIsPie) return draftPalette.map((_, i) => `Colour ${i + 1}`);
		const bound = (chartDraft['data-comp-y-field'] ?? '')
			.split(',')
			.map((f) => f.trim())
			.filter(Boolean);
		// One picker before anything is bound, so the colour is settable while the chart is being set
		// up rather than only afterwards.
		return bound.length ? bound : ['Series 1'];
	});

	/** Set one series' colour. PADDED FROM THE TENANT PALETTE rather than left sparse: the list is
	 *  positional, so a hole would shift every colour after it onto the wrong series. */
	function setDraftColour(index: number, value: string): void {
		const next = [...draftPalette];
		while (next.length <= index) {
			next.push(tenantPalette[next.length % tenantPalette.length] ?? '#0f766e');
		}
		next[index] = value;
		chartDraft['data-comp-colors'] = next.join(',');
	}

	// Everything a loop, table or chart can bind to: the built-in per-client collections (the
	// client's accounts) followed by the tenant's own datasets. One list, because the three source
	// pickers below must offer the same choices — a collection bindable by a repeat but not by a
	// table is a difference an author reads as a bug.
	const collections = $derived(collectionOptions(datasetOptions));

	// Columns for a collection code — a firm dataset (the page resolves each static dataset's column
	// schema from its published sample) or a built-in. Unknown columns (a SELECT *, a not-yet-parsed
	// source) yield [] — the field pickers then show nothing rather than guess.
	function datasetColumns(code: string): string[] {
		return collections.find((d) => d.code === code)?.columns ?? [];
	}

	/** The `— Choose … —` placeholder plus one item per collection, keeping a stored code that
	 *  matches nothing selectable and flagged, so an old template surfaces the problem instead of
	 *  silently losing its binding. Shared by the repeat, chart and data-table pickers, which had
	 *  three copies of it. */
	function collectionItems(chosen: string, placeholder: string) {
		return [
			{ value: '', label: placeholder },
			...collections.map((d) => ({ value: d.code, label: `${d.name} (${d.code})` })),
			...(chosen && !collections.some((d) => d.code === chosen)
				? [{ value: chosen, label: `${chosen} (unknown source)` }]
				: [])
		];
	}
	const availableCols = $derived(
		chartDraft['data-comp-source-mode'] === 'manual'
			? manualColumns.filter(Boolean)
			: datasetColumns(chartDraft['data-comp-data-source'] ?? '')
	);
	// Known columns for the jinja-table's chosen dataset (from the real datasetOptions).
	const jtDatasetCols = $derived<string[]>(datasetColumns(jtDataset));
	const draftYFields = $derived((chartDraft['data-comp-y-field'] ?? '').split(',').filter(Boolean));
	const draftColumns = $derived((chartDraft['data-comp-columns'] ?? '').split(',').filter(Boolean));

	function addYField(col: string): void {
		if (!draftYFields.includes(col))
			chartDraft['data-comp-y-field'] = [...draftYFields, col].join(',');
	}
	function removeYField(col: string): void {
		chartDraft['data-comp-y-field'] = draftYFields.filter((c) => c !== col).join(',');
	}
	function setXField(col: string): void {
		chartDraft['data-comp-x-field'] = chartDraft['data-comp-x-field'] === col ? '' : col;
	}
	function setLabelField(col: string): void {
		chartDraft['data-comp-label-field'] = chartDraft['data-comp-label-field'] === col ? '' : col;
	}
	function setValueField(col: string): void {
		chartDraft['data-comp-value-field'] = chartDraft['data-comp-value-field'] === col ? '' : col;
	}
	function addColumn(col: string): void {
		if (!draftColumns.includes(col))
			chartDraft['data-comp-columns'] = [...draftColumns, col].join(',');
	}
	function removeColumn(col: string): void {
		chartDraft['data-comp-columns'] = draftColumns.filter((c) => c !== col).join(',');
	}

	// Serialise the manual grid into the draft whenever it changes (manual mode only), so Apply
	// persists it via data-comp-inline-data. Filled rows only — blank trailing rows are dropped.
	$effect(() => {
		if (chartDraft['data-comp-source-mode'] !== 'manual') return;
		const filled = manualRows.filter((r) => r.some((c) => c.trim() !== ''));
		chartDraft['data-comp-inline-data'] = JSON.stringify({ columns: manualColumns, rows: filled });
	});

	// ── Chart config live preview ────────────────────────────────────────────────
	// Resolve the selected dataset's REAL rows (the same snapshot the render worker uses) and draw
	// the chart inline, so a blank/"no data" preview surfaces a dataset that isn't linking up — the
	// point of showing it here. Only the dataset + subject drive a refetch; type/field tweaks
	// re-render from the cached rows.
	let previewRows = $state<Record<string, unknown>[]>([]);
	let previewLoading = $state(false);
	let previewError = $state<string | null>(null);
	let previewContactId = $state<number | null>(null);

	const previewDataset = $derived(
		datasetOptions.find((d) => d.code === (chartDraft['data-comp-data-source'] ?? ''))
	);
	// ONE MAPPING with the canvas component (`previewCompFromAttrs`). Both read the same
	// `data-comp-*` attributes and must draw the same picture: a second copy here is how this drawer
	// ends up promising a chart the page does not show.
	//
	// NO PALETTE ARGUMENT, and it needs none: `chartDraftFrom` seeds the firm's own palette onto the
	// component, so the colours are an explicit choice the worker reads rather than an editor-side
	// fallback it cannot know about. What is left here is the fallback for a component carrying no
	// colours at all, and that must be the WORKER'S palette (`DEFAULT_PALETTE`, kept equal to
	// charts.py by the contract test) — a preview is a claim about the output.
	const previewComp = $derived(previewCompFromAttrs(chartDraft));
	// SAMPLE ROWS — `sampleTableRows`, the very ones the canvas table preview draws, keyed off the
	// column NAMES so an amount reads as money and a fund as a fund name. They stand in when the
	// binding resolves nothing: a dataset that 404s or genuinely has no rows used to leave this panel
	// blank under a red line, which answers "is my binding broken" and nothing else. An author is
	// also here to see the SHAPE, and sample values show it.
	//
	// They are LABELLED as sample and shown BESIDE the real reason, never instead of it. Sample data
	// passing for resolved data is the one thing this must not do — the same rule the client portal's
	// `resolveSlices` keeps, for the same reason.
	const previewSampleRows = $derived.by(() => {
		const cols = previewDataset?.columns ?? [];
		if (!cols.length) return [] as Record<string, unknown>[];
		return sampleTableRows(cols, 5).map(
			(r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])) as Record<string, unknown>
		);
	});
	const previewSampled = $derived(
		!previewLoading && previewRows.length === 0 && previewSampleRows.length > 0
	);
	/** What the panel actually draws. */
	const previewShownRows = $derived(previewSampled ? previewSampleRows : previewRows);
	const previewSvg = $derived(renderChartSvg(previewComp, previewShownRows, 600, 340));
	// A TABLE'S PREVIEW IS REAL TOO. The modal used to carry a second panel, also headed "Preview",
	// showing a static picture of the chosen type — so a chart had two previews, one true and one
	// decorative, and a table had only the decorative one. This renders the resolved rows through
	// `tablePreviewInner`, the same builder the canvas table uses, so what is shown is what the
	// binding actually yields: no columns chosen, or a column that is not in the dataset, shows as
	// the blank it will be rather than as sample text.
	const previewTableHtml = $derived.by(() => {
		const cols = (chartDraft['data-comp-columns'] ?? '')
			.split(',')
			.map((c) => c.trim())
			.filter(Boolean);
		if (!cols.length) return '';
		const headers = (chartDraft['data-comp-headers'] ?? '').split(',').map((h) => h.trim());
		const design: JDesign = {
			...defaultJDesign(),
			headerBg: chartDraft['data-comp-header-bg'] ?? '#1e3a5f',
			headerFg: chartDraft['data-comp-header-fg'] ?? '#ffffff',
			striped: (chartDraft['data-comp-striped'] ?? 'true') !== 'false'
		};
		const rows = previewShownRows.slice(0, 8).map((row) =>
			cols.map((c) => {
				const v = (row as Record<string, unknown>)[c];
				return v == null ? '' : String(v);
			})
		);
		return tablePreviewInner(
			design,
			cols.map((c, i) => headers[i] || c),
			rows
		);
	});
	// Field bindings absent from the resolved dataset — the classic "linking" bug, surfaced plainly.
	const previewMissingFields = $derived.by(() => {
		const cols = previewDataset?.columns ?? [];
		if (!cols.length) return [] as string[];
		const used = draftIsPie
			? [chartDraft['data-comp-label-field'], chartDraft['data-comp-value-field']]
			: [chartDraft['data-comp-x-field'], ...draftYFields];
		return used.filter((f): f is string => !!f && !cols.includes(f));
	});

	$effect(() => {
		if (!chartConfigOpen || draftIsTable) {
			previewRows = [];
			return;
		}
		// Manual mode: preview straight from the local grid (there is no dataset to resolve).
		if (chartDraft['data-comp-source-mode'] === 'manual') {
			const cols = manualColumns.filter(Boolean);
			previewRows = manualRows
				.filter((r) => r.some((c) => c.trim() !== ''))
				.map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i] ?? ''])));
			previewLoading = false;
			previewError = null;
			return;
		}
		const uid = previewDataset?.uid;
		const contact = previewContactId;
		if (!uid || !resolvePreviewData) {
			previewRows = [];
			return;
		}
		let cancelled = false;
		previewLoading = true;
		previewError = null;
		resolvePreviewData(uid, contact)
			.then((d) => {
				if (cancelled) return;
				const cols = d.columns ?? [];
				previewRows = (d.rows ?? []).map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
			})
			.catch((e) => {
				if (!cancelled) {
					// Surface the endpoint's real reason (bad SQL / scope mismatch / no rows) — the whole
					// point of this preview is to diagnose a broken binding.
					previewError =
						e instanceof Error && e.message ? e.message : 'Could not resolve this dataset.';
					previewRows = [];
				}
			})
			.finally(() => {
				if (!cancelled) previewLoading = false;
			});
		return () => {
			cancelled = true;
		};
	});

	let blockSearch = $state('');
	function filterBlocks(): void {
		if (!blocksEl) return;
		const q = blockSearch.trim().toLowerCase();
		// The GrapesJS layout-fix CSS uses display:flex!important on .gjs-block, so a plain
		// inline style.display assignment loses the cascade — must use !important priority.
		blocksEl.querySelectorAll<HTMLElement>('.gjs-block').forEach((el) => {
			const label = el.querySelector('.gjs-block-label')?.textContent?.toLowerCase() ?? '';
			if (!q || label.includes(q)) {
				el.style.removeProperty('display');
			} else {
				el.style.setProperty('display', 'none', 'important');
			}
		});
		blocksEl.querySelectorAll<HTMLElement>('.gjs-block-category').forEach((cat) => {
			const grid = cat.querySelector<HTMLElement>('.gjs-blocks-c');
			const hasVisible = [...cat.querySelectorAll<HTMLElement>('.gjs-block')].some(
				(b) => b.style.getPropertyPriority('display') !== 'important'
			);
			if (q) {
				// Force-open the grid so matching blocks inside collapsed categories are visible.
				if (grid) grid.style.setProperty('display', hasVisible ? 'grid' : 'none', 'important');
				cat.style.display = hasVisible ? '' : 'none';
			} else {
				// Let GrapesJS's own open/close state take back control.
				if (grid) grid.style.removeProperty('display');
				cat.style.display = '';
			}
		});
	}

	const pageIndices = $derived(Array.from({ length: pageCount }, (_, i) => i));
	/** A tenant theme colour as a plain `#rrggbb`.
	 *
	 *  THE HEX CONVERSION IS THE POINT, not tidiness. `getComputedStyle().color` answers in the colour
	 *  space the value was AUTHORED in, and the theme's tokens are `oklch()` — so this used to hand back
	 *  `oklch(0.64 0.115 202)`, which went straight into `data-comp-colors`, into the saved component,
	 *  and out as `fill="oklch(…)"` in the SVG the render worker bakes into the PDF. The editor drew it
	 *  correctly and the document did not, which is the exact failure `chart-preview.contract.test.ts`
	 *  refuses for the chart's own chrome. It also read as `oklch(0.64 0.115 202)` inside the colour
	 *  picker's hex field, where nobody could do anything with it.
	 *
	 *  Canvas does the conversion: assigning any colour the browser understands to `fillStyle` and
	 *  reading it back gives `#rrggbb`. A value it cannot parse leaves `fillStyle` at the colour before
	 *  it, so the black seed is what tells us the assignment did nothing. */
	function resolveCssColour(varName: string): string {
		if (typeof document === 'undefined') return '';
		const probe = document.createElement('span');
		probe.style.cssText = `color:var(${varName});position:absolute;left:-9999px`;
		document.body.appendChild(probe);
		const computed = getComputedStyle(probe).color;
		probe.remove();
		if (!computed) return '';
		const ctx = document.createElement('canvas').getContext('2d');
		if (!ctx) return computed;
		ctx.fillStyle = '#000000';
		ctx.fillStyle = computed;
		const hex = ctx.fillStyle;
		// `#000000` back from a non-black input means the browser rejected it — keep what we had
		// rather than turning the firm's brand colour black.
		return typeof hex === 'string' && (hex !== '#000000' || computed.includes('0, 0, 0'))
			? hex
			: computed;
	}
	onMount(() => {
		const lead = [resolveCssColour('--brand'), resolveCssColour('--primary')].filter(Boolean);
		const fill = ['#3b82f6', '#f59e0b', '#8b5cf6', '#10b981', '#ef4444', '#ec4899'];
		tenantPalette = [...new Set([...lead, ...fill])].slice(0, 8);
	});
	// UI mock for the Theme dropdown — real values come from tenant settings once the API lands.
	const themeBrand = [
		{ label: 'Primary', hex: '#1e3a5f' },
		{ label: 'Secondary', hex: '#e8eaee' }
	];
	const themeChartColours = ['#b3c5dc', '#8aa5c6', '#5f83ad', '#3d6491', '#1e3a5f'];

	let linkedDatasets = $state<string[]>([]);
	let addDatasetValue = $state('');

	function addLinkedDataset(code: string): void {
		if (code && !linkedDatasets.includes(code)) linkedDatasets = [...linkedDatasets, code];
		addDatasetValue = '';
	}
	const removeLinkedDataset = (code: string) =>
		(linkedDatasets = linkedDatasets.filter((d) => d !== code));

	// The linked input form's fields (the `formFields` prop, resolved from `input_form_code` by
	// the page — docs/forms.md §9.1) are the single variables usable as {{ tokens }} in the
	// document — they feed the canvas autocomplete and the jinja-var chip picker. Deduped by
	// key (a key can only map to one value).
	const formVars = $derived.by(() => {
		const seen = new Set<string>();
		const out: { key: string; label: string }[] = [];
		for (const f of formFields) {
			if (f.key && !seen.has(f.key)) {
				seen.add(f.key);
				out.push({ key: f.key, label: f.label });
			}
		}
		return out;
	});

	// Core merge-field catalogue — client / adviser / firm / system fields present in EVERY template,
	// so advisers PICK canonical variables rather than hand-typing tokens that drift between advisers
	// and can't be populated. Keys align with the preview's sample data (app/render/sample.py) so a
	// preview fills each one.
	const CORE_VARIABLES: { key: string; label: string; group: string }[] = [
		{ key: 'client.name', label: 'Client name', group: 'Client' },
		{ key: 'client.first_name', label: 'First name', group: 'Client' },
		{ key: 'client.surname', label: 'Surname', group: 'Client' },
		{ key: 'client.email', label: 'Email', group: 'Client' },
		// `client.phone` keeps its key and now resolves to the PRIMARY MOBILE — the landline
		// column it used to read is gone. The key did not change, so a template already
		// holding it still fills; only the label here tells an author what they are inserting.
		{ key: 'client.phone', label: 'Mobile', group: 'Client' },
		{ key: 'client.telephone', label: 'Phone', group: 'Client' },
		{ key: 'client.address', label: 'Address', group: 'Client' },
		{ key: 'client.date_of_birth', label: 'Date of birth', group: 'Client' },
		{ key: 'adviser.name', label: 'Adviser name', group: 'Adviser' },
		{ key: 'adviser.email', label: 'Adviser email', group: 'Adviser' },
		{ key: 'firm.name', label: 'Firm name', group: 'Firm' },
		{ key: 'firm.frn', label: 'FCA firm reference number', group: 'Firm' },
		{ key: 'firm.chn', label: 'Companies House number', group: 'Firm' },
		{ key: 'today', label: "Today's date", group: 'System' },
		{ key: 'document_name', label: 'Document name', group: 'System' },
		// The client's book, rolled up — the values a covering paragraph needs without a loop
		// ("your 3 accounts, totalling £277,550, as at 30 June 2026"). The accounts THEMSELVES are a
		// collection, so they are offered below as loop fields rather than as insertable chips: a
		// bare `{{ accounts }}` in prose would print a Python list into a client's document.
		{ key: 'portfolio.total_value', label: 'Total portfolio value', group: 'Portfolio' },
		{ key: 'portfolio.account_count', label: 'Number of accounts', group: 'Portfolio' },
		{ key: 'portfolio.currency', label: 'Portfolio currency', group: 'Portfolio' },
		{ key: 'portfolio.as_at', label: 'Valued as at', group: 'Portfolio' }
	];

	// The full pickable catalogue: core fields + the linked form's answers, grouped for the picker
	// (and flattened for the canvas autocomplete). The single set advisers insert variables from.
	const catalogueGroups = $derived.by(() => {
		const groups: { group: string; vars: { key: string; label: string }[] }[] = [];
		for (const v of CORE_VARIABLES) {
			let g = groups.find((x) => x.group === v.group);
			if (!g) {
				g = { group: v.group, vars: [] };
				groups.push(g);
			}
			g.vars.push({ key: v.key, label: v.label });
		}
		if (formVars.length) {
			groups.push({
				group: 'Form answers',
				vars: formVars.map((v) => ({ key: v.key, label: v.label }))
			});
		}
		// The tenant's datasets contribute their columns as `row.<col>` tokens (used inside a
		// repeat/table that loops the dataset). All datasets are offered — binding is component-level,
		// not tracked in linked_datasets — and the lint validates row.<col> against the real loop.
		//
		// `datasetOptions`, deliberately NOT `collections`: `datasetVariableGroups` also emits a
		// `one.<code>.<col>` group, and `one` is the first row of each resolved DATASET
		// (`worker.py` → `first_rows(datasets)`). A built-in collection is not in that map, so
		// `one.accounts.name` would be offered by the picker and render blank.
		for (const g of datasetVariableGroups(datasetOptions)) groups.push(g);
		// The built-ins get the row-field half only, for that reason. The group name states the loop
		// requirement, as the dataset groups' do — a bare `row.value` dropped into prose renders
		// blank, and the lint can only flag the stray `row` root after the fact.
		for (const c of BUILTIN_COLLECTIONS)
			groups.push({
				group: `${c.name} — row field (inside a repeat loop)`,
				vars: c.columns.map((col) => ({ key: `row.${col}`, label: col }))
			});
		return groups;
	});
	// Flattened for the canvas autocomplete, each var carrying its group so the dropdown can render
	// section headers (e.g. "Client", "Form answers", "Fund Holdings · row"). A `row.<col>` shared by
	// two datasets legitimately appears under each — the loop it sits in decides which one resolves.
	const catalogueFlat = $derived(
		catalogueGroups.flatMap((g) =>
			g.vars.map((v) => ({ key: v.key, label: v.label, group: g.group }))
		)
	);

	// ── Logic blocks (Repeat / Conditional) ─────────────────────────────────────
	// Both are structural — they emit {% for %} / {% if %} and carry no visual box of their own — so
	// instead of GrapesJS trait widgets they get bespoke panels (like the image / page panels). The
	// loop source and the condition are stored as data-* attributes the component's toHTML reads
	// (component-types.ts). The extra data-cond-* attributes are editor-only (the conditional's toHTML
	// emits only the {% if %}, never the wrapper div), so they round-trip the builder inputs without
	// leaking into the generated document.
	const isRepeatSelected = $derived<boolean>(selectedComp?.get?.('type') === 'repeat');
	const isConditionalSelected = $derived<boolean>(selectedComp?.get?.('type') === 'conditional');

	// Repeat: which dataset the loop iterates. Inside the loop, fields are `row.<col>` — the same
	// convention as the data table and the variable catalogue, so catalogue `row.*` tokens resolve.
	let repeatDataset = $state('');
	$effect(() => {
		// Re-read when a different repeat is selected (Backbone reads aren't tracked, so the effect
		// keys off the reactive selectedComp / isRepeatSelected).
		if (!isRepeatSelected || !selectedComp) return;
		const attrs = (selectedComp.getAttributes?.() ?? {}) as Record<string, string>;
		repeatDataset = attrs['data-loop-key'] ?? '';
	});
	const repeatColumns = $derived(datasetColumns(repeatDataset));
	function setRepeatDataset(code: string): void {
		repeatDataset = code;
		selectedComp?.addAttributes?.({ 'data-loop-key': code });
		editorTick++;
	}

	// Conditional: a simple field/operator/value builder (or a raw Jinja expression). The computed
	// boolean expression is written to data-condition; the builder inputs are stored alongside so the
	// panel repopulates on reselect.
	const COND_OPS: { value: string; label: string; needsValue: boolean }[] = [
		{ value: 'set', label: 'Is set (not empty)', needsValue: false },
		{ value: 'empty', label: 'Is empty', needsValue: false },
		{ value: 'eq', label: 'Equals', needsValue: true },
		{ value: 'ne', label: 'Does not equal', needsValue: true },
		{ value: 'gt', label: 'Greater than', needsValue: true },
		{ value: 'lt', label: 'Less than', needsValue: true },
		{ value: 'contains', label: 'Contains', needsValue: true }
	];
	let condMode = $state<'simple' | 'advanced'>('simple');
	let condField = $state('');
	let condOp = $state('set');
	let condValue = $state('');
	let condAction = $state<'show' | 'hide'>('show');
	let condHasElse = $state(false);
	let condRaw = $state('');
	// Fields a condition can test: the single-value catalogue tokens (core context + `one.<ds>.<col>`),
	// never `row.*` (a condition is evaluated once, outside any loop).
	const conditionFields = $derived(catalogueFlat.filter((v) => !v.key.startsWith('row.')));
	const condNeedsValue = $derived(COND_OPS.find((o) => o.value === condOp)?.needsValue ?? false);
	// Live preview of the emitted guard (reads the builder state, so it updates as the author types —
	// independent of when the attribute is committed to the component).
	const condExprPreview = $derived.by(() => {
		const e = condExpr();
		if (!e) return '';
		return condAction === 'hide' ? `not (${e})` : e;
	});
	$effect(() => {
		if (!isConditionalSelected || !selectedComp) return;
		const a = (selectedComp.getAttributes?.() ?? {}) as Record<string, string>;
		condAction = a['data-condition-action'] === 'hide' ? 'hide' : 'show';
		condHasElse = a['data-has-else'] === 'true';
		condMode = a['data-cond-mode'] === 'advanced' ? 'advanced' : 'simple';
		condField = a['data-cond-field'] ?? '';
		condOp = a['data-cond-op'] || 'set';
		condValue = a['data-cond-value'] ?? '';
		condRaw = a['data-condition'] ?? '';
	});
	function condExpr(): string {
		if (condMode === 'advanced') return condRaw.trim();
		const f = condField.trim();
		if (!f) return '';
		const v = condValue.trim();
		const numeric = v !== '' && !Number.isNaN(Number(v));
		const q = numeric ? v : `'${v.replace(/'/g, "\\'")}'`;
		switch (condOp) {
			case 'set':
				return f;
			case 'empty':
				return `not ${f}`;
			case 'eq':
				return `${f} == ${q}`;
			case 'ne':
				return `${f} != ${q}`;
			case 'gt':
				return `${f} > ${q}`;
			case 'lt':
				return `${f} < ${q}`;
			case 'contains':
				return `${q} in ${f}`;
			default:
				return f;
		}
	}
	function applyCondition(): void {
		if (!selectedComp) return;
		selectedComp.addAttributes?.({
			'data-condition': condExpr(),
			'data-condition-action': condAction,
			'data-cond-mode': condMode,
			'data-cond-field': condField,
			'data-cond-op': condOp,
			'data-cond-value': condValue
		});
		editorTick++;
	}
	function setCondHasElse(on: boolean): void {
		condHasElse = on;
		if (!selectedComp) return;
		selectedComp.addAttributes?.({ 'data-has-else': on ? 'true' : 'false' });
		let elseComp: { remove?: () => void } | null = null;
		selectedComp.components?.()?.each?.((c: { getAttributes?: () => Record<string, string> }) => {
			const ca = (c.getAttributes?.() ?? {}) as Record<string, string>;
			if (ca['data-branch'] === 'else') elseComp = c as { remove?: () => void };
		});
		if (on && !elseComp) {
			selectedComp.append?.(
				`<div data-gjs-type="cond-branch" data-branch="else" style="padding:16px 12px 12px;min-height:56px;border-top:1px dashed #f59e0b;margin-top:8px;"></div>`
			);
		} else if (!on && elseComp) {
			(elseComp as { remove?: () => void }).remove?.();
		}
		editorTick++;
	}

	// ── Text styles (named typographic presets) ─────────────────────────────────
	// One-click paragraph styles so authors get consistent typography without setting font-size /
	// weight / spacing by hand on every element (the dominant text-styling friction). Applied as
	// inline styles — the editor's styling model is inline/id-based (the class manager is deliberately
	// hidden), so they serialise with the element and render in the PDF. A common BASE resets the
	// properties a previous preset might have left behind, so switching presets is deterministic.
	const isTextSelected = $derived<boolean>(selectedComp?.get?.('type') === 'text');
	const TEXT_STYLE_BASE: Record<string, string> = {
		// Baseline every preset resets, so switching presets never leaves a previous one's weight /
		// style / border behind (presets override what they care about).
		'font-weight': '400',
		'font-style': 'normal',
		'letter-spacing': 'normal',
		'text-transform': 'none',
		'border-left': 'none',
		'padding-left': '0',
		color: 'inherit'
	};
	const TEXT_STYLES: { key: string; label: string; style: Record<string, string> }[] = [
		{
			key: 'title',
			label: 'Title',
			style: {
				...TEXT_STYLE_BASE,
				'font-size': '28px',
				'font-weight': '700',
				'line-height': '1.2',
				margin: '0 0 8px'
			}
		},
		{
			key: 'heading',
			label: 'Heading',
			style: {
				...TEXT_STYLE_BASE,
				'font-size': '20px',
				'font-weight': '700',
				'line-height': '1.25',
				margin: '16px 0 6px'
			}
		},
		{
			key: 'subheading',
			label: 'Subheading',
			style: {
				...TEXT_STYLE_BASE,
				'font-size': '13px',
				'font-weight': '600',
				'line-height': '1.3',
				'letter-spacing': '.04em',
				'text-transform': 'uppercase',
				color: '#64748b',
				margin: '12px 0 4px'
			}
		},
		{
			key: 'body',
			label: 'Body text',
			style: {
				...TEXT_STYLE_BASE,
				'font-size': '13px',
				'font-weight': '400',
				'line-height': '1.6',
				margin: '0 0 8px'
			}
		},
		{
			key: 'small',
			label: 'Small / caption',
			style: {
				...TEXT_STYLE_BASE,
				'font-size': '11px',
				'font-weight': '400',
				'line-height': '1.5',
				color: '#64748b',
				margin: '0 0 6px'
			}
		},
		{
			key: 'quote',
			label: 'Quote',
			style: {
				...TEXT_STYLE_BASE,
				'font-size': '14px',
				'font-style': 'italic',
				'line-height': '1.6',
				'border-left': '3px solid #cbd5e1',
				'padding-left': '12px',
				color: '#475569',
				margin: '8px 0'
			}
		}
	];
	let textStylePick = $state('');
	function applyTextStyle(key: string): void {
		textStylePick = '';
		const preset = TEXT_STYLES.find((s) => s.key === key);
		if (!preset || !selectedComp) return;
		selectedComp.addStyle?.(preset.style);
		editorTick++;
	}

	// Live editor lint: {{ tokens }} with no data source (common context + attached form fields +
	// datasets), recomputed (debounced) from the SERIALISED template. buildSchema keeps the
	// {% for %} loops that the canvas HTML strips, so dataset row.* accesses aren't false-flagged.
	// The client-side companion of the server generate gate — a live authoring nudge, not the block.
	let liveUnsourced = $state<string[]>([]);
	let unsourcedTimer: ReturnType<typeof setTimeout> | null = null;
	$effect(() => {
		void editorTick; // recompute once canvas edits settle
		void rteActive; // …but re-arm when the caret leaves a text box (see below)
		if (unsourcedTimer) clearTimeout(unsourcedTimer);
		unsourcedTimer = setTimeout(() => {
			// Serialising while a text box is being edited re-renders it and resets the caret, so hold
			// off — rte:disable bumps editorTick and this recomputes from the committed text.
			if (!editor || rteActive) return;
			try {
				const built = buildSchema(editor, schema, layout) as { html?: unknown };
				const html = typeof built.html === 'string' ? built.html : '';
				// Column schema per real dataset code, so the lint can validate `row.<col>` accesses
				// (empty columns stay unchecked — the lint is soft about unknown schemas).
				// Built-ins included, so a mistyped `row.valu` inside a loop over the client's accounts
				// is flagged the same way a mistyped dataset column is.
				const dsCols: Record<string, string[]> = {};
				for (const d of collections) if (d.columns.length) dsCols[d.code] = d.columns;
				liveUnsourced = unsourcedTokens(
					html,
					formVars.map((v) => v.key),
					dsCols
				);
			} catch {
				liveUnsourced = [];
			}
		}, 400);
	});

	/** Push the current variable list into the canvas iframe so the autocomplete
	    can offer suggestions when the user types {{ in a text block. */
	function sendVarsToCanvas(): void {
		if (!editor) return;
		try {
			const iframe = editor.Canvas?.getFrameEl?.() as HTMLIFrameElement | null;
			if (iframe?.contentWindow) {
				iframe.contentWindow.postMessage(
					{
						type: 'gjs-vars-update',
						vars: catalogueFlat.map((v) => ({ key: v.key, label: v.label, group: v.group })),
						// Offered by the canvas autocomplete after a `|` inside a {{ token }}.
						filters: JINJA_FILTERS.map((f) => ({ label: f.label, snippet: f.snippet }))
					},
					'*'
				);
			}
		} catch {
			/* iframe not ready yet */
		}
	}

	$effect(() => {
		// Re-run whenever the variable catalogue changes; sendVarsToCanvas() reads catalogueFlat.
		void catalogueFlat;
		sendVarsToCanvas();
	});
</script>

{#snippet editPreviewToggle()}
	<Segmented
		options={[
			{ value: 'edit', label: 'Edit' },
			{ value: 'preview', label: 'Preview' }
		]}
		value={previewMode ? 'preview' : 'edit'}
		onValueChange={(v) => {
			if ((v === 'preview') !== previewMode) void togglePreview();
		}}
	/>
{/snippet}

<!-- The variable catalogue (searchable, grouped) — shared by the toolbar Insert-variable popover and
     the right-click Insert-variable dialog. Selecting inserts the {{ token }}. -->
{#snippet variableCatalogue()}
	<Command.Root class="rounded-lg">
		<Command.Input placeholder="Search variables…" bind:value={insertFieldQuery} />
		<Command.List>
			<Command.Empty>No variables match “{insertFieldQuery}”.</Command.Empty>
			{#each catalogueGroups as group (group.group)}
				<Command.Group heading={group.group}>
					{#each group.vars as v (v.key)}
						<Command.Item value={`${v.key} ${v.label}`} onSelect={() => insertField(v.key)}>
							<span class="text-primary shrink-0 font-mono text-xs">{v.key}</span>
							<span class="text-muted-foreground min-w-0 flex-1 truncate text-xs">{v.label}</span>
						</Command.Item>
					{/each}
				</Command.Group>
			{/each}
		</Command.List>
	</Command.Root>
{/snippet}

<!-- Right-click → Insert variable opens this modal picker (see ctxInsertVariable). -->
<Dialog.Root bind:open={insertVarDialogOpen}>
	<Dialog.Content class="gap-0 overflow-hidden p-0 sm:max-w-md">
		<Dialog.Header class="border-b px-4 py-3 text-left">
			<Dialog.Title class="flex items-center gap-2 text-sm">
				<BracesIcon class="text-primary size-4" /> Insert variable
			</Dialog.Title>
		</Dialog.Header>
		{@render variableCatalogue()}
	</Dialog.Content>
</Dialog.Root>

<svelte:window
	onkeydown={(e) => {
		if (e.key === 'Escape' && ctxMenu) closeContextMenu();
	}}
/>

<!-- overflow-hidden: this fixed-height surface is an app-like shell whose rails and canvas
     each scroll internally (min-h-0 + overflow-y-auto). Clipping here stops a few px of
     accidental rail/canvas overflow from giving the shell its own scrollbar — the stray
     bar that "hangs near the end" when wheeling over the canvas. -->
<div class="flex h-[calc(100svh-var(--topbar-h,4rem))] flex-col overflow-hidden">
	{#if ctxMenu}
		<!-- Context-menu backdrop: any click / right-click elsewhere dismisses it -->
		<button
			type="button"
			class="fixed inset-0 z-50 cursor-default"
			aria-label="Dismiss menu"
			onmousedown={closeContextMenu}
			oncontextmenu={(e) => {
				e.preventDefault();
				closeContextMenu();
			}}
		></button>
		<div
			bind:this={ctxMenuEl}
			class="bg-popover fixed z-50 flex max-h-[min(70vh,34rem)] w-56 flex-col overflow-y-auto rounded-lg border py-1 shadow-xl"
			style="left:{ctxMenu.x}px; top:{ctxMenu.y}px;"
			role="menu"
			tabindex="-1"
		>
			{#if ctxHasComp}
				<p
					class="text-muted-foreground truncate px-3 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-wide"
				>
					{ctxCompName}
				</p>
				<button
					type="button"
					role="menuitem"
					class="hover:bg-muted flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm"
					onclick={ctxInsertVariable}
				>
					<BracesIcon class="size-3.5 shrink-0 opacity-70" /> Insert variable
				</button>
				<button
					type="button"
					role="menuitem"
					class="hover:bg-muted flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm"
					onclick={ctxDuplicate}
				>
					<CopyIcon class="size-3.5 shrink-0 opacity-70" /> Duplicate
				</button>
				<button
					type="button"
					role="menuitem"
					class="text-destructive hover:bg-destructive/10 flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm"
					onclick={ctxDelete}
				>
					<Trash2Icon class="size-3.5 shrink-0" /> Delete
				</button>
				<div class="bg-border my-1 h-px"></div>
			{/if}
			<p
				class="text-muted-foreground px-3 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-wide"
			>
				Insert block
			</p>
			{#each ctxBlockGroups as group (group.label)}
				<p
					class="text-muted-foreground/70 px-3 pb-0.5 pt-1.5 text-[9px] font-semibold uppercase tracking-wide"
				>
					{group.label}
				</p>
				{#each group.blocks as block (block.id)}
					<button
						type="button"
						role="menuitem"
						class="hover:bg-muted flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm"
						onclick={() => insertBlockFromMenu(block.id)}
					>
						<span
							class="flex h-4 w-6 shrink-0 items-center justify-center text-muted-foreground [&>svg]:max-h-4 [&>svg]:max-w-full"
						>
							<!-- eslint-disable-next-line svelte/no-at-html-tags -- trusted static block icon SVG defined in blocks.ts -->
							{@html block.media}
						</span>
						{block.label}
					</button>
				{/each}
			{/each}
		</div>
	{/if}
	<!-- Bar 1: title + actions -->
	<div class="flex items-center gap-3 border-b px-4 py-2">
		<div class="flex min-w-0 flex-1 items-center gap-1.5">
			{#if backHref}
				<Button variant="ghost" size="icon-sm" href={backHref} aria-label="Back to template">
					<ArrowLeftIcon class="size-4" />
				</Button>
			{/if}
			<div class="min-w-0">
				<p class="truncate text-sm font-semibold">{name}</p>
				<p class="text-muted-foreground text-xs">
					Template editor{saving ? ' · Saving…' : savedAt ? ` · Saved ${savedAt}` : ''}
				</p>
			</div>
			{#if readOnly}
				<span
					class="bg-muted text-muted-foreground shrink-0 rounded px-2 py-0.5 text-xs font-medium"
				>
					View only
				</span>
			{/if}
		</div>
		<div class="flex shrink-0 items-center gap-1.5">
			{#if !versionPreview && !readOnly}
				{@render editPreviewToggle()}
				<!-- Divider separates the view-mode toggle from the authoring/file clusters -->
				<div class="bg-border mx-0.5 h-4 w-px"></div>
				<!-- Insert a variable ({{ token }}) from the catalogue. Drops it at the caret in the text box
				     being edited (via the canvas bridge), or appends it to the selected block. -->
				<Popover.Root bind:open={insertFieldOpen}>
					<Popover.Trigger
						class={buttonVariants({ variant: 'outline', size: 'sm' })}
						title="Insert a variable"
					>
						<BracesIcon class="size-4" />
						Insert variable
					</Popover.Trigger>
					<Popover.Content align="end" class="w-72 p-0">
						{@render variableCatalogue()}
					</Popover.Content>
				</Popover.Root>
				<div class="bg-border mx-0.5 h-4 w-px"></div>
				<Button
					variant="ghost"
					size="icon-sm"
					onclick={() => importFileEl?.click()}
					aria-label="Import template"
					title="Import template"
				>
					<UploadIcon class="size-4" />
				</Button>
				<Button
					variant="ghost"
					size="icon-sm"
					onclick={exportTemplate}
					aria-label="Export template"
					title="Export template"
				>
					<DownloadIcon class="size-4" />
				</Button>
				<Button variant="outline" size="sm" onclick={() => void save()} disabled={saving}>
					<SaveIcon class="size-4" />
					{saving ? 'Saving…' : 'Save'}
				</Button>
			{/if}
		</div>
	</div>

	<!-- Version preview banner -->
	{#if versionPreview}
		<div
			class="border-warning/30 bg-warning/10 flex items-center justify-between gap-4 border-b px-4 py-2"
		>
			<div class="flex items-center gap-1.5">
				<HistoryIcon class="text-warning size-4 shrink-0" />
				<Button
					variant="ghost"
					size="icon-sm"
					aria-label="Older version"
					disabled={versionIndex >= historyEntries.length - 1}
					onclick={() =>
						versionIndex < historyEntries.length - 1 &&
						switchVersion(historyEntries[versionIndex + 1]!)}
					class="text-warning hover:bg-warning/15 hover:text-warning disabled:opacity-30"
					><ChevronLeftIcon class="size-4" /></Button
				>
				<span class="text-warning text-sm">
					<strong>v{versionPreview.version.versionNumber} · {versionPreview.version.status}</strong>
					<span class="text-warning/70 mx-1">·</span>
					<span class="text-warning"
						>{new Date(versionPreview.version.savedAt).toLocaleString('en-GB', {
							dateStyle: 'medium',
							timeStyle: 'short'
						})}</span
					>
					{#if diffStats && (diffStats.restored > 0 || diffStats.changed > 0)}
						<span class="text-warning/50 mx-1.5">·</span>
						{#if diffStats.restored > 0}
							<span
								class="bg-success/15 text-success mr-1 inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
							>
								<span class="bg-success inline-block size-2 rounded-full"></span>
								{diffStats.restored} restored
							</span>
						{/if}
						{#if diffStats.changed > 0}
							<span
								class="bg-warning/15 text-warning inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
							>
								<span class="bg-warning inline-block size-2 rounded-full"></span>
								{diffStats.changed} changed
							</span>
						{/if}
					{/if}
				</span>
				<Button
					variant="ghost"
					size="icon-sm"
					aria-label="Newer version"
					disabled={versionIndex <= 0}
					onclick={() => versionIndex > 0 && switchVersion(historyEntries[versionIndex - 1]!)}
					class="text-warning hover:bg-warning/15 hover:text-warning disabled:opacity-30"
					><ChevronRightIcon class="size-4" /></Button
				>
				<span class="text-warning/50 mx-0.5">|</span>
				<button
					type="button"
					onclick={() => {
						historyOpen = true;
					}}
					class="text-warning text-xs underline-offset-2 hover:underline">Browse all</button
				>
			</div>
			<div class="flex shrink-0 items-center gap-2">
				<Button variant="outline" size="sm" onclick={cancelVersionPreview}>← Back to current</Button
				>
				<Button variant="brand" size="sm" onclick={restoreVersion}>Restore this version</Button>
			</div>
		</div>
	{/if}

	<!-- Body: the canvas column, with BOTH RAILS FLOATING OVER THE CANVAS — the email editor's
	     posture (`email-editor.svelte`'s `.rail-right`, whose values these are). A panel resting on
	     the workspace reads as a tool you can put away, where a strip welded to the window edge reads
	     as part of the window.
	     Three surfaces, in order page (`--card`) · rail chrome (the 97% mix) · backdrop (`--muted`):
	     a rail painted in `--card` reads as part of the document rather than as a tool on top of it.
	     The mix is toward `--foreground` so ONE declaration is correct in both themes — in light the
	     page is the lightest thing so the mix darkens, in dark it is the darkest so the same mix
	     lightens. `in oklab`, never `oklch` (`theme.css` records that hue interpolation drags a mixed
	     neutral toward blue). -->
	<div class="flex min-h-0 flex-1">
		<!-- Canvas column: sub-bar + canvas. Bordered and clipped like the rails so the three read as
		     one composition, but no elevation — it is the ground being worked on, not a tool over it. -->
		<div class="flex min-w-0 flex-1 flex-col">
			<!-- Sub-bar: undo/theme/history · page nav · zoom -->
			<div class="bg-card flex shrink-0 items-center border-b px-3 py-1" class:pr-11={previewMode}>
				<div class="flex flex-1 items-center gap-0.5">
					{#if previewMode}
						<span class="text-brand flex items-center gap-1.5 text-xs font-medium">
							<EyeIcon class="size-3.5" /> Previewing
						</span>
						<Button
							variant="ghost"
							size="icon-sm"
							class="ml-1"
							aria-label="Refresh preview"
							title="Refresh preview"
							disabled={docPreviewLoading}
							onclick={() => void renderDocPreview(true)}
						>
							<RotateCwIcon class="size-3.5 {docPreviewLoading ? 'animate-spin' : ''}" />
						</Button>
					{:else}
						<!-- +/× toggle: swaps the left rail between Pages/Layers and Blocks -->
						<Button
							variant={leftView === 'blocks' ? 'secondary' : 'brand'}
							size="icon-sm"
							aria-label={leftView === 'blocks' ? 'Close blocks' : 'Insert blocks'}
							title={leftView === 'blocks' ? 'Back to pages & layers' : 'Insert blocks'}
							onclick={() => (leftView = leftView === 'blocks' ? 'outline' : 'blocks')}
						>
							{#if leftView === 'blocks'}
								<XIcon class="size-4" />
							{:else}
								<PlusIcon class="size-4" />
							{/if}
						</Button>
						<div class="bg-border mx-1.5 h-4 w-px"></div>
						<div class="flex items-center">
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Undo"
								title="Undo (Ctrl+Z)"
								onclick={undo}
							>
								<Undo2Icon class="size-4" />
							</Button>
							<DropdownMenu.Root bind:open={undoOpen} onOpenChange={(o) => o && openUndoMenu()}>
								<DropdownMenu.Trigger
									class="text-muted-foreground hover:bg-muted hover:text-foreground -ml-0.5 flex h-6 items-center rounded px-0.5"
									aria-label="Undo history"
									title="Undo history"
								>
									<ChevronDownIcon class="size-3" />
								</DropdownMenu.Trigger>
								<DropdownMenu.Content align="start" class="w-56 p-0">
									{#if undoList.length === 0}
										<p class="text-muted-foreground p-3 text-xs">Nothing to undo.</p>
									{:else}
										<div
											class="max-h-72 overflow-y-auto py-1"
											role="group"
											onmouseleave={() => (undoHover = -1)}
										>
											{#each undoList as entry, i (i)}
												<button
													type="button"
													class={[
														'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs',
														i <= undoHover ? 'bg-muted' : 'hover:bg-muted/60'
													]}
													onmouseenter={() => (undoHover = i)}
													onclick={() => undoTo(i)}
												>
													<Undo2Icon class="text-muted-foreground size-3 shrink-0" />
													<span class="truncate">{entry.label}</span>
												</button>
											{/each}
										</div>
										<div class="text-muted-foreground border-t px-3 py-1.5 text-[11px]">
											{undoHover >= 0
												? `Undo ${undoHover + 1} action${undoHover === 0 ? '' : 's'}`
												: `${undoList.length} action${undoList.length === 1 ? '' : 's'} available`}
										</div>
									{/if}
								</DropdownMenu.Content>
							</DropdownMenu.Root>
						</div>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label="Redo"
							title="Redo (Ctrl+Shift+Z)"
							onclick={redo}
						>
							<Redo2Icon class="size-4" />
						</Button>
						<div class="bg-border mx-1.5 h-4 w-px"></div>
						<!-- Live variable lint: tokens with no data source (would render blank + are blocked
						     at generate). Hover for the list; fix by picking from the insert list instead. -->
						{#if liveUnsourced.length}
							<span
								class="text-warning flex items-center gap-1 text-xs font-medium"
								title={`No data source for: ${liveUnsourced.join(', ')} — pick variables from the insert list (form fields + linked datasets). Unsourced tokens render blank and are blocked at generate.`}
							>
								⚠ {liveUnsourced.length} unsourced {liveUnsourced.length === 1
									? 'variable'
									: 'variables'}
							</span>
							<div class="bg-border mx-1.5 h-4 w-px"></div>
						{/if}
						<!-- Overflow indicator: content taller than one sheet will split across pages at
						     generation — surfaced live in the editor (measured in the canvas), not only at
						     preview/generate. -->
						{#if overflowPages.length}
							<span
								class="text-warning flex items-center gap-1 text-xs font-medium"
								title={`Content is taller than one page on page ${overflowPages
									.map((i) => i + 1)
									.join(
										', '
									)} — it will split across pages when generated. Trim content, or add a page.`}
							>
								⚠ Overflows page {overflowPages.map((i) => i + 1).join(', ')}
							</span>
							<div class="bg-border mx-1.5 h-4 w-px"></div>
						{/if}
						<!-- Theme button hidden: theming is driven by per-tenant branding. -->
						{#if false}
							<div class="relative">
								<Button
									variant="ghost"
									size="sm"
									onclick={() => {
										themeOpen = !themeOpen;
										historyOpen = false;
									}}
								>
									<PaletteIcon class="size-4" /> Theme
								</Button>
								{#if themeOpen}
									<div
										class="bg-popover absolute left-0 top-full z-50 mt-1 w-56 rounded-lg border p-3 shadow-lg"
									>
										<p
											class="text-muted-foreground mb-2 text-[10px] font-semibold uppercase tracking-wide"
										>
											Brand
										</p>
										<div class="flex gap-3">
											{#each themeBrand as { label, hex } (label)}
												<div class="flex flex-col items-start gap-1">
													<span
														class="size-7 rounded-md border shadow-sm"
														style="background:{hex}"
														title={hex}
													></span>
													<span class="text-muted-foreground text-[10px]">{label}</span>
												</div>
											{/each}
										</div>
										<p
											class="text-muted-foreground mb-2 mt-3 text-[10px] font-semibold uppercase tracking-wide"
										>
											Chart colors
										</p>
										<div class="flex gap-1.5">
											{#each themeChartColours as hex, i (hex)}
												<div class="flex flex-col items-center gap-1">
													<span
														class="size-7 rounded-md border shadow-sm"
														style="background:{hex}"
														title={hex}
													></span>
													<span class="text-muted-foreground text-[10px] tabular-nums">{i + 1}</span
													>
												</div>
											{/each}
										</div>
										<button
											type="button"
											class="text-muted-foreground hover:text-foreground mt-3 text-xs font-medium transition-colors"
											>Change in Settings →</button
										>
									</div>
								{/if}
							</div>
						{/if}
						<DropdownMenu.Root bind:open={historyOpen}>
							<DropdownMenu.Trigger class={buttonVariants({ variant: 'ghost', size: 'sm' })}>
								<HistoryIcon class="size-4" /> History
							</DropdownMenu.Trigger>
							<DropdownMenu.Content align="start" class="w-64 p-0">
								{#if historyEntries.length === 0}
									<p class="text-muted-foreground p-3 text-xs">
										No saved versions yet. Save the template to create a version.
									</p>
								{:else}
									<div class="max-h-72 overflow-y-auto">
										{#each historyEntries as version, i (version.uid)}
											{@const isActive = versionPreview?.version.uid === version.uid}
											<div
												class="hover:bg-muted flex items-center justify-between gap-2 px-3 py-2 {isActive
													? 'bg-warning/10'
													: ''}"
											>
												<div class="min-w-0">
													<p class="truncate text-xs font-medium">
														v{version.versionNumber}
														<span class="text-muted-foreground font-normal">· {version.status}</span
														>
														{#if i === 0}<span class="text-muted-foreground font-normal"
																>· current</span
															>{/if}
													</p>
													<p class="text-muted-foreground text-[11px]">
														{new Date(version.savedAt).toLocaleString('en-GB', {
															dateStyle: 'short',
															timeStyle: 'short'
														})}{version.author ? ` · ${version.author}` : ''}
													</p>
												</div>
												{#if isActive}
													<span class="text-warning shrink-0 text-[11px] font-medium">Viewing</span>
												{:else}
													<Button
														variant="outline"
														size="sm"
														class="shrink-0"
														onclick={() =>
															versionPreview ? switchVersion(version) : previewVersion(version)}
													>
														Preview
													</Button>
												{/if}
											</div>
										{/each}
									</div>
								{/if}
							</DropdownMenu.Content>
						</DropdownMenu.Root>
					{/if}
				</div>
				<div class="flex items-center gap-0.5">
					<Button
						variant="ghost"
						size="icon-sm"
						aria-label="Previous page"
						disabled={activePage <= 0}
						onclick={() => goToPage(activePage - 1)}
					>
						<ChevronLeftIcon class="size-4" />
					</Button>
					<span class="text-muted-foreground min-w-16 text-center text-xs font-medium tabular-nums">
						Page {activePage + 1} / {pageCount}
					</span>
					<Button
						variant="ghost"
						size="icon-sm"
						aria-label="Next page"
						disabled={activePage >= pageCount - 1}
						onclick={() => goToPage(activePage + 1)}
					>
						<ChevronRightIcon class="size-4" />
					</Button>
				</div>
				<div class="flex flex-1 items-center justify-end gap-0.5">
					<!-- Offered when READ-ONLY too: reading and copying the markup of a template you may
					     only read is a real thing to want. What read-only withholds is the unlock. -->
					<Button
						variant="ghost"
						size="icon-sm"
						aria-label="View HTML"
						title="View the document's HTML"
						aria-pressed={codeOpen}
						class={codeOpen ? 'bg-brand text-brand-foreground hover:bg-brand' : ''}
						onclick={() => (codeOpen = !codeOpen)}
					>
						<CodeXmlIcon class="size-4" />
					</Button>
					<div class="bg-border mx-1 h-4 w-px"></div>
					<Button variant="ghost" size="icon-sm" aria-label="Zoom out canvas" onclick={zoomOut}>
						<MinusIcon class="size-4" />
					</Button>
					<button
						type="button"
						onclick={zoomReset}
						title="Reset to 100%"
						class="text-muted-foreground min-w-11 text-center text-xs font-medium tabular-nums"
						>{zoom}%</button
					>
					<Button variant="ghost" size="icon-sm" aria-label="Zoom in canvas" onclick={zoomIn}>
						<PlusIcon class="size-4" />
					</Button>
				</div>
			</div>
			<!-- Canvas, and the POSITIONING CONTEXT for both rails: they float over THIS box and not over
			     the body row, so neither covers the sub-bar above. GrapesJS scrolls inside its own frame, so
			     this box does not scroll and a rail anchored to it stays put.
			     NOTHING IS RESERVED for either rail: a centred page leaves a gutter wider than a rail at any
			     width the page fits in, and below that the page overflows and the canvas scrolls. `pr-8` is
			     the preview close strip's own room. -->
			<div class="relative min-h-0 flex-1 {previewMode ? 'bg-muted pr-8' : 'bg-muted/40'}">
				<div bind:this={canvasEl} class="h-full w-full"></div>
				<!-- Left rail (hidden in preview) -->
				<aside
					class="bg-[color-mix(in_oklab,var(--card)_97%,var(--foreground))] absolute bottom-3 left-3 top-3 z-20 flex w-64 flex-col
						overflow-hidden rounded-xl shadow-[var(--elev-2)] [outline:1px_solid_color-mix(in_oklab,var(--foreground)_10%,transparent)] [outline-offset:-1px]"
					class:hidden={previewMode}
				>
					<div class="min-h-0 flex-1 overflow-y-auto">
						<!-- Outline view: collapsible Pages + Layers sections -->
						<div class:hidden={leftView !== 'outline'} class="flex flex-col">
							<!-- Pages section -->
							<div class="border-b">
								<div class="flex items-center px-3 py-1.5">
									<button
										type="button"
										onclick={() => (pagesOpen = !pagesOpen)}
										class="text-foreground flex items-center gap-1.5 text-left"
										aria-expanded={pagesOpen}
									>
										<FilesIcon class="size-3.5 shrink-0" />
										<span class="text-[11px] font-bold uppercase tracking-wide"
											>Pages ({pageCount})</span
										>
									</button>
									<!-- Add page sits next to the label so it can't be mistaken for the
									     accordion chevron, which stays alone on the right. -->
									<button
										type="button"
										aria-label="Add page"
										title="Add page"
										class="text-muted-foreground hover:text-foreground hover:bg-brand/10 ml-1 rounded p-0.5"
										onclick={newPage}
									>
										<PlusIcon class="size-3.5" />
									</button>
									<div class="flex-1"></div>
									<button
										type="button"
										onclick={() => (pagesOpen = !pagesOpen)}
										aria-label={pagesOpen ? 'Collapse pages' : 'Expand pages'}
										class="text-muted-foreground hover:text-foreground"
									>
										<ChevronDownIcon
											class="size-3 shrink-0 transition-transform {pagesOpen ? '' : '-rotate-90'}"
										/>
									</button>
								</div>
								{#if pagesOpen}
									<div class="space-y-1 px-3 pb-2">
										{#each pageIndices as i (i)}
											<div
												draggable="true"
												role="listitem"
												class="group flex items-center gap-1 rounded-md transition-colors {activePage ===
												i
													? 'bg-brand text-brand-foreground'
													: 'text-foreground/70 hover:text-foreground hover:bg-brand/10'}
													{dragOverIndex === i && dragFromIndex !== i ? 'ring-brand ring-2 ring-offset-1' : ''}"
												ondragstart={(e) => onDragStart(e, i)}
												ondragover={(e) => onDragOver(e, i)}
												ondrop={(e) => onDrop(e, i)}
												ondragend={onDragEnd}
											>
												<span
													class="cursor-grab px-1 opacity-20 transition-opacity group-hover:opacity-60 active:cursor-grabbing"
													aria-hidden="true"
												>
													<GripVerticalIcon class="size-3.5" />
												</span>
												<button
													type="button"
													onclick={() => goToPage(i)}
													class="flex flex-1 items-center gap-2 py-1.5 pr-2 text-left text-sm"
												>
													<FileIcon class="size-3.5 shrink-0 opacity-70" />
													<span class="text-xs opacity-70">{i + 1}</span>
													<span class="font-medium">{pageNames[i] ?? `Page ${i + 1}`}</span>
												</button>
												<button
													type="button"
													aria-label="Duplicate page {i + 1}"
													onclick={() => copyPage(i)}
													class="hover:text-foreground px-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
													title="Duplicate page"
												>
													<CopyIcon class="size-3" />
												</button>
												{#if pageCount > 1}
													<button
														type="button"
														aria-label="Delete page {i + 1}"
														onclick={() => deletePage(i)}
														class="hover:text-destructive px-2 text-sm opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
														title="Delete page">×</button
													>
												{/if}
											</div>
										{/each}
									</div>
								{/if}
							</div>
							<!-- Layers section -->
							<div class="flex flex-col border-t">
								<button
									type="button"
									onclick={() => (layersOpen = !layersOpen)}
									class="text-foreground flex items-center gap-1.5 px-3 py-1.5 text-left"
									aria-expanded={layersOpen}
								>
									<LayersIcon class="size-3.5 shrink-0" />
									<span class="flex-1 text-[11px] font-bold uppercase tracking-wide">Layers</span>
									<ChevronDownIcon
										class="size-3 shrink-0 transition-transform {layersOpen ? '' : '-rotate-90'}"
									/>
								</button>
								<div
									id="gjs-layers-panel"
									bind:this={layersEl}
									class:hidden={!layersOpen}
									class="overflow-y-auto border-t"
								></div>
							</div>
						</div>
						<!-- Blocks view: searchable palette. blocksEl stays mounted (editor init reads it). -->
						<div class:hidden={leftView !== 'blocks'} class="flex flex-col">
							<div class="border-b px-3 py-2">
								<input
									type="search"
									placeholder="Search blocks…"
									bind:value={blockSearch}
									oninput={filterBlocks}
									class="border-border placeholder:text-muted-foreground focus:border-brand h-7 w-full rounded-md border bg-transparent px-2 text-xs outline-none transition-colors focus:ring-2 focus:ring-[color-mix(in_oklch,var(--brand)_15%,transparent)]"
								/>
							</div>
							<div id="gjs-blocks-panel" bind:this={blocksEl} class="overflow-y-auto"></div>
						</div>
					</div>
				</aside>
				<!-- Right rail (hidden in preview, and put away by its own collapse button). 320px is the
				     email rail's measured width: `PropertyRow`'s label column is a fixed 6.5rem, so
				     narrowing the rail takes the difference out of the CONTROL's share and a two-word
				     label ('Corner radius', 'Border collapse') stops setting on one line. -->
				<!-- HIDDEN, NEVER DESTROYED — the mechanism preview already uses. GrapesJS is handed
				     `traitsEl` itself (`traitManager: { appendTo }`) and `#gjs-styles-panel`/`diagEl` are in
				     here too, so an `{#if}` around this rail would leave GrapesJS appending into detached
				     nodes and the traits panel would come back permanently blank; a rail shut at mount would
				     also fail `create`'s five-element guard and never initialise at all. -->
				<aside
					class="bg-[color-mix(in_oklab,var(--card)_97%,var(--foreground))] absolute bottom-3 right-3 top-3 z-20 flex w-80 flex-col
						overflow-hidden rounded-xl shadow-[var(--elev-2)] [outline:1px_solid_color-mix(in_oklab,var(--foreground)_10%,transparent)] [outline-offset:-1px]"
					class:hidden={previewMode || !railOpen}
				>
					<!-- RAIL HEAD, the email editor's shape (`email-editor.svelte`'s `.rail-head`): an icon, the
					     name of what the rail is acting on, and the actions that belong to the rail rather than
					     to any one section. It STAYS PUT while the sections scroll, which is the point — the
					     target's name was previously the first row of the scroll area, so it left the screen as
					     soon as you reached the properties it names, and a rail that does not say what it is
					     editing is how you restyle the wrong element.
					     The collapse button lives here for the same reason: putting the whole rail away is a
					     rail action, not one belonging to any section below. -->
					<div class="flex min-h-9 flex-none items-center gap-1.5 border-b px-3 py-2">
						{#if isPageSelected}
							<FileTextIcon class="text-muted-foreground size-3.5 shrink-0" />
						{:else if selectedComp || jinjaVarKey !== null}
							<BoxIcon class="text-brand size-3.5 shrink-0" />
						{:else}
							<PaletteIcon class="text-muted-foreground size-3.5 shrink-0" />
						{/if}
						<span class="min-w-0 flex-1 truncate text-xs font-semibold">{railTitle}</span>
						{#if selectedComp && !hideStyle && !multiSelect}
							<!-- CSS mode lives in the head, not beside the target chips: it switches what the WHOLE
							     rail below shows, so it is a rail action. -->
							<Button
								variant="ghost"
								size="icon-sm"
								title="Toggle CSS"
								aria-label="Toggle CSS"
								aria-pressed={cssMode}
								class={cssMode ? 'bg-brand text-brand-foreground hover:bg-brand' : ''}
								onclick={toggleCssMode}
							>
								<BracesIcon class="size-3.5" />
							</Button>
						{/if}
						<Button
							variant="ghost"
							size="icon-sm"
							title="Hide the style panel"
							aria-label="Hide the style panel"
							onclick={() => (railOpen = false)}
						>
							<PanelRightIcon class="size-3.5" />
						</Button>
					</div>
					<div class="min-h-0 flex-1 overflow-y-auto">
						<!-- The element's config (or the page's). Its header is the rail HEAD above, which stays
						     put while this scrolls — see the note there. -->
						<div class="flex flex-col">
							<div>
								<!-- Selected element config: traits + style. A page is a component too — its
								     page-layout options are doc-page traits (see component-types.ts). -->
								<div>
									{#if !selectedComp && jinjaVarKey === null}
										<div
											class="text-muted-foreground flex min-h-[70svh] flex-col items-center justify-center gap-2 p-6 text-center"
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												width="24"
												height="24"
												viewBox="0 0 24 24"
												fill="none"
												stroke="currentColor"
												stroke-width="1.5"
												stroke-linecap="round"
												stroke-linejoin="round"
												class="opacity-40"
												aria-hidden="true"
											>
												<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" />
											</svg>
											<p class="text-xs">
												Select a component on the canvas to view and edit its settings.
											</p>
										</div>
									{:else}
										<!-- The SELECTOR detail, not the target's name — the head above carries that now, and
										     showing it twice made the first two rows of the rail say the same thing. What is
										     left is the part the head cannot hold: the tag and classes a style rule will
										     actually match, and the names of a multi-selection, which has no single name.
										     Rendered only when there is something to say. -->
										{#if selectedCount > 1 || targetTag || targetClasses.length}
											<div class="flex flex-wrap items-center gap-1.5 border-b px-3 py-2">
												{#if selectedCount > 1}
													{#each selectedNames as n, i (i)}
														<span
															class="bg-brand text-brand-foreground rounded px-1.5 py-0.5 text-[11px] font-medium"
															>{n}</span
														>
													{/each}
												{:else}
													{#if targetTag}
														<span class="text-muted-foreground font-mono text-[10px]"
															>{'<'}{targetTag}{'>'}</span
														>
													{/if}
													{#each targetClasses as c (c)}
														<span
															class="bg-muted text-muted-foreground rounded px-1 py-0.5 font-mono text-[10px]"
															>.{c}</span
														>
													{/each}
												{/if}
											</div>
										{/if}
										<!-- The chart's (or table's) OWN SECTION. This was a bare bordered row wedged between
										     sectors: loose chrome sitting where a section belongs, and it holds the only thing
										     about a chart there is to set. `set` is the binding, so the dot answers the same
										     question the canvas caption does — a chart with no source is the one the strip
										     warns about. Single selection only; the config is per element. -->
										{#if compType && !multiSelect}
											<InspectorSector label={compTypeLabel} set={compIsBound} class="border-b">
												<div class="p-2.5">
													<Button
														variant="outline"
														size="sm"
														class="w-full"
														onclick={openChartConfig}
													>
														Configure {compTypeLabel}
													</Button>
												</div>
											</InspectorSector>
										{/if}
										<!-- Jinja-var variable picker — replaces the GrapesJS trait widget -->
										{#if jinjaVarKey !== null && !multiSelect}
											<div class="space-y-2 border-b p-2.5">
												<div class="space-y-1">
													<Label
														for="jinja-var-picker"
														class="text-muted-foreground text-xs font-medium">Variable</Label
													>
													<SelectMenu
														id="jinja-var-picker"
														items={catalogueGroups.map((g) => ({
															label: g.group,
															options: g.vars.map((v) => ({
																value: v.key,
																label: v.label || v.key
															}))
														}))}
														value={splitExpr(jinjaVarKey ?? '').base}
														onValueChange={jinjaSetBase}
														placeholder="— select variable —"
													/>
												</div>
												<div class="flex items-center justify-between gap-2">
													<Label class="text-muted-foreground text-[11px] font-medium"
														>Filters</Label
													>
													{#if availableFilters(splitExpr(jinjaVarKey ?? '').filters).length}
														<SelectMenu
															items={availableFilters(splitExpr(jinjaVarKey ?? '').filters).map(
																(f) => ({
																	value: f.snippet,
																	label: f.label
																})
															)}
															bind:value={jinjaFilterPick}
															onValueChange={(v) => {
																jinjaAddFilter(v);
																jinjaFilterPick = '';
															}}
															placeholder="ƒ Add filter"
															size="sm"
															class="w-auto"
														/>
													{:else}
														<span class="text-muted-foreground/60 text-[10px]">All added</span>
													{/if}
												</div>
												{#if splitExpr(jinjaVarKey ?? '').filters.length}
													<div class="flex flex-wrap items-center gap-1">
														{#each splitExpr(jinjaVarKey ?? '').filters as f, fi (fi)}
															<span
																class="bg-brand/10 text-brand inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px]"
															>
																{f}
																<button
																	type="button"
																	onclick={() => jinjaRemoveFilter(fi)}
																	aria-label="Remove filter"
																	class="hover:text-destructive leading-none">×</button
																>
															</span>
														{/each}
													</div>
												{/if}
												{#if jinjaVarKey}
													<p class="text-muted-foreground font-mono text-[10px]">
														{'{{ '}{jinjaVarKey}{' }}'}
													</p>
												{/if}
												{#if splitExpr(jinjaVarKey ?? '').base.startsWith('row.')}
													<p class="text-muted-foreground text-[10px] leading-tight">
														Dataset column — resolves inside a repeat/table that loops this dataset.
													</p>
												{/if}
											</div>
										{/if}
									{/if}
									<!-- Page settings — bespoke shadcn controls in place of the doc-page's GrapesJS
									     trait widgets (paper size / orientation / margins), plus the per-page
									     header & footer editor. -->
									{#if isPageSelected}
										<InspectorSector label="Page setup" bind:open={pageSetupOpen} class="border-b">
											<div class="space-y-3 px-3 pb-3">
												{#if pageCount > 1}
													<!-- In the BODY, not the header: `InspectorSector` owns the header row, and this
													     only ever showed while the section was open anyway. -->
													<button
														type="button"
														onclick={applyPageSetupToAll}
														title="Apply this page's setup (size, orientation, margins, background, border) to all pages"
														class="text-brand/80 hover:text-brand hover:bg-brand/10 -mt-1 rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors"
														>Apply to all pages</button
													>
												{/if}
												<div class="space-y-1.5">
													<Label
														for="page-size-picker"
														class="text-muted-foreground text-[11px] font-medium">Paper size</Label
													>
													<SelectMenu
														id="page-size-picker"
														items={PAGE_SIZE_OPTS.map(([value, label]) => ({ value, label }))}
														value={pageDraft.pageSize}
														onValueChange={(v) => setPageChoice('pageSize', v)}
													/>
												</div>
												<div class="space-y-1.5">
													<Label class="text-muted-foreground text-[11px] font-medium"
														>Orientation</Label
													>
													<Segmented
														class="w-full"
														options={ORIENTATION_OPTS.map(([value, label]) => ({ value, label }))}
														value={pageDraft.pageOrientation}
														onValueChange={(v) => setPageChoice('pageOrientation', v)}
													/>
												</div>
												<div class="space-y-1.5">
													<Label class="text-muted-foreground text-[11px] font-medium"
														>Margins (mm)</Label
													>
													<!-- Quick presets — apply a common margin to all four sides in one click. -->
													<div class="flex flex-wrap gap-1">
														{#each MARGIN_PRESETS as p (p.label)}
															<button
																type="button"
																onclick={() => setAllMargins(p.mm)}
																title="Set all margins to {p.mm}mm"
																class="border-input text-muted-foreground hover:border-brand hover:text-brand rounded border px-2 py-0.5 text-[10px] font-medium transition-colors"
																>{p.label}</button
															>
														{/each}
													</div>
													<!-- Draggable page preview — drag any edge to set that margin. -->
													<div
														bind:this={diagEl}
														class="border-success/40 bg-success/10 relative mx-auto select-none overflow-hidden rounded border {marginDrag
															? 'cursor-grabbing'
															: ''}"
														style="width:{diagW}px; height:{diagH}px;"
													>
														<div
															class="bg-success/30 absolute inset-x-0 top-0"
															style="height:{marginPct.t}%"
														></div>
														<div
															class="bg-success/30 absolute inset-x-0 bottom-0"
															style="height:{marginPct.b}%"
														></div>
														<div
															class="bg-success/30 absolute inset-y-0 left-0"
															style="width:{marginPct.l}%"
														></div>
														<div
															class="bg-success/30 absolute inset-y-0 right-0"
															style="width:{marginPct.r}%"
														></div>
														<!-- The content area, on a themed surface — NOT `bg-doc-paper`. This diagram is a
															     schematic (its page body is `bg-success/10`, its margins `bg-success/30`), not a
															     paper preview, so it follows the app scheme. It previously drew the label as
															     `text-success/60`, which composited to 2.3:1 on the pale fill in light and 1.6:1 in
															     dark — measured, and illegible. Muted ink on the app surface is legible in both. -->
														<div
															class="border-success/40 text-muted-foreground bg-background/90 absolute flex items-center justify-center rounded-sm border border-dashed text-[8px] font-medium"
															style="top:{marginPct.t}%;right:{marginPct.r}%;bottom:{marginPct.b}%;left:{marginPct.l}%"
														>
															content
														</div>
														<div
															role="separator"
															aria-label="Top margin"
															aria-orientation="horizontal"
															class="absolute inset-x-0 z-10 flex cursor-ns-resize items-center justify-center"
															style="top:calc({marginPct.t}% - 4px); height:8px;"
															onpointerdown={(e) => startMarginDrag(e, 0)}
														>
															<div
																class="h-px w-full transition-colors {marginDrag?.side === 0
																	? 'bg-success'
																	: 'bg-success/50 hover:bg-success/70'}"
															></div>
														</div>
														<div
															role="separator"
															aria-label="Bottom margin"
															aria-orientation="horizontal"
															class="absolute inset-x-0 z-10 flex cursor-ns-resize items-center justify-center"
															style="bottom:calc({marginPct.b}% - 4px); height:8px;"
															onpointerdown={(e) => startMarginDrag(e, 2)}
														>
															<div
																class="h-px w-full transition-colors {marginDrag?.side === 2
																	? 'bg-success'
																	: 'bg-success/50 hover:bg-success/70'}"
															></div>
														</div>
														<div
															role="separator"
															aria-label="Left margin"
															aria-orientation="vertical"
															class="absolute inset-y-0 z-10 flex cursor-ew-resize items-center justify-center"
															style="left:calc({marginPct.l}% - 4px); width:8px;"
															onpointerdown={(e) => startMarginDrag(e, 3)}
														>
															<div
																class="h-full w-px transition-colors {marginDrag?.side === 3
																	? 'bg-success'
																	: 'bg-success/50 hover:bg-success/70'}"
															></div>
														</div>
														<div
															role="separator"
															aria-label="Right margin"
															aria-orientation="vertical"
															class="absolute inset-y-0 z-10 flex cursor-ew-resize items-center justify-center"
															style="right:calc({marginPct.r}% - 4px); width:8px;"
															onpointerdown={(e) => startMarginDrag(e, 1)}
														>
															<div
																class="h-full w-px transition-colors {marginDrag?.side === 1
																	? 'bg-success'
																	: 'bg-success/50 hover:bg-success/70'}"
															></div>
														</div>
													</div>
													<div class="grid grid-cols-2 gap-2">
														{#each MARGIN_FIELDS as m (m.prop)}
															<div
																class="border-input focus-within:border-brand flex h-8 items-center rounded-md border bg-transparent pl-2 transition-colors"
															>
																<span
																	class="text-muted-foreground w-11 shrink-0 text-[10px] font-semibold uppercase tracking-wide"
																	>{m.label}</span
																>
																<input
																	type="number"
																	min="0"
																	value={pageDraft[m.prop]}
																	onchange={(e: Event) =>
																		setPageMargin(
																			m.prop,
																			Number((e.target as HTMLInputElement).value)
																		)}
																	aria-label="{m.label} margin (mm)"
																	class="text-foreground h-full w-full min-w-0 bg-transparent px-1 text-right text-sm tabular-nums outline-none"
																/>
																<span class="text-muted-foreground px-2 text-[10px] font-medium"
																	>mm</span
																>
															</div>
														{/each}
													</div>
												</div>
											</div>
										</InspectorSector>
									{/if}
									{#if isPageSelected}
										<!-- Header & footer editor -->
										<InspectorSector
											label="Header &amp; footer"
											bind:open={headerFooterOpen}
											class="border-b"
										>
											<div class="space-y-3 px-3 pb-3">
												{#if pageCount > 1}
													<button
														type="button"
														onclick={applyHeaderFooterToAll}
														title="Apply this page's header & footer to all pages"
														class="text-brand/80 hover:text-brand hover:bg-brand/10 -mt-1 rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors"
														>Apply to all pages</button
													>
												{/if}
												<Segmented
													class="w-full"
													options={[
														{ value: 'header', label: 'Header' },
														{ value: 'footer', label: 'Footer' }
													]}
													value={hfSub}
													onValueChange={(v) => (hfSub = v as 'header' | 'footer')}
												/>

												{#snippet hfPanel(
													def: import('./editor/types').FooterDef,
													onRemove: () => void
												)}
													<div class="divide-y rounded-md border">
														<div class="space-y-3 p-3">
															<div class="flex items-center justify-between">
																<p
																	class="text-muted-foreground text-[11px] font-semibold uppercase tracking-wide"
																>
																	Content
																</p>
																<button
																	type="button"
																	class="text-muted-foreground hover:text-destructive text-xs transition-colors"
																	onclick={onRemove}>Remove</button
																>
															</div>
															<div class="space-y-2">
																<div class="flex items-center justify-between">
																	<Label class="text-muted-foreground text-[10px] font-medium"
																		>Columns</Label
																	>
																	<div class="flex items-center gap-0.5">
																		<button
																			type="button"
																			disabled={def.columns.length <= 1}
																			onclick={() => {
																				def.columns.splice(hfSelCol, 1);
																				hfSelCol = Math.min(hfSelCol, def.columns.length - 1);
																			}}
																			class="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-30"
																			aria-label="Remove selected column"
																			><MinusIcon class="size-3" /></button
																		>
																		<button
																			type="button"
																			disabled={def.columns.length >= 5}
																			onclick={() => {
																				def.columns.push({ content: '', align: 'left', fr: 1 });
																				hfSelCol = def.columns.length - 1;
																			}}
																			class="text-muted-foreground hover:text-foreground rounded p-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-30"
																			aria-label="Add column"><PlusIcon class="size-3" /></button
																		>
																	</div>
																</div>
																<div class="flex h-10 overflow-hidden rounded-md border">
																	{#each def.columns as col, i (i)}
																		<button
																			type="button"
																			style="flex:{col.fr || 1}"
																			onclick={() => (hfSelCol = i)}
																			class="flex min-w-0 flex-col items-center justify-center gap-0.5 border-r px-1 transition-colors last:border-r-0 {i ===
																			hfSelCol
																				? 'bg-brand/10 text-brand'
																				: 'text-muted-foreground hover:bg-muted/50'}"
																		>
																			<span class="text-[9px] font-semibold uppercase">{i + 1}</span
																			>
																			<span
																				class="w-full truncate text-center text-[9px] leading-none opacity-60"
																				>{col.content || '—'}</span
																			>
																		</button>
																	{/each}
																</div>
																{#if def.columns[hfSelCol]}
																	{@const col = def.columns[hfSelCol]!}
																	<div class="bg-muted/20 space-y-2 rounded-md border p-3">
																		<div class="flex items-center gap-2">
																			<span
																				class="text-muted-foreground shrink-0 text-[10px] font-medium"
																				>Align</span
																			>
																			<div class="bg-muted flex rounded-md p-0.5">
																				{#each [['left', 'L'], ['center', 'C'], ['right', 'R']] as [a, label] (a)}
																					<button
																						type="button"
																						onclick={() =>
																							(col.align = a as 'left' | 'center' | 'right')}
																						class="flex size-6 items-center justify-center rounded text-xs font-medium transition-all {col.align ===
																						a
																							? 'bg-brand text-brand-foreground shadow-sm'
																							: 'text-muted-foreground hover:text-foreground'}"
																						>{label}</button
																					>
																				{/each}
																			</div>
																			<span
																				class="text-muted-foreground ml-auto shrink-0 text-[10px] font-medium"
																				>Width</span
																			>
																			<div class="flex items-center gap-1">
																				<input
																					type="number"
																					min="1"
																					max="10"
																					value={col.fr || 1}
																					onchange={(e: Event) => {
																						col.fr = Math.max(
																							1,
																							Math.min(
																								10,
																								Number((e.target as HTMLInputElement).value) || 1
																							)
																						);
																					}}
																					class="focus:border-brand h-6 w-10 rounded border bg-transparent px-1 text-center text-xs outline-none"
																					aria-label="Column width fraction"
																				/>
																				<span class="text-muted-foreground text-[10px]">fr</span>
																			</div>
																		</div>
																		<Textarea
																			rows={4}
																			placeholder={'Text or {{ variable }}'}
																			aria-label="Column content"
																			class="placeholder:text-muted-foreground/40 min-h-[80px] resize-y font-mono text-xs leading-relaxed"
																			value={col.content}
																			onchange={(e: Event) =>
																				(col.content = (e.target as HTMLTextAreaElement).value)}
																		/>
																	</div>
																{/if}
															</div>
														</div>
														<div class="space-y-3 p-3">
															<p
																class="text-muted-foreground text-[11px] font-semibold uppercase tracking-wide"
															>
																Appearance
															</p>
															<div class="grid grid-cols-2 gap-2">
																<div class="space-y-1">
																	<Label class="text-muted-foreground text-[10px] font-medium"
																		>Font size</Label
																	>
																	<Input
																		value={def.fontSize || '12px'}
																		placeholder="12px"
																		class="h-7 px-2 font-mono text-xs"
																		onchange={(e: Event) =>
																			(def.fontSize = (e.target as HTMLInputElement).value)}
																	/>
																</div>
																<div class="space-y-1">
																	<Label class="text-muted-foreground text-[10px] font-medium"
																		>Padding (v)</Label
																	>
																	<Input
																		value={def.paddingV || '8px'}
																		placeholder="8px"
																		class="h-7 px-2 font-mono text-xs"
																		onchange={(e: Event) =>
																			(def.paddingV = (e.target as HTMLInputElement).value)}
																	/>
																</div>
															</div>
															<div class="grid grid-cols-3 gap-2">
																<div class="space-y-1">
																	<Label class="text-muted-foreground text-[10px] font-medium"
																		>Text</Label
																	>
																	<ColorInput
																		value={def.color || '#6b7280'}
																		swatches={tenantPalette}
																		onchange={(v) => (def.color = v)}
																	/>
																</div>
																<div class="space-y-1">
																	<Label class="text-muted-foreground text-[10px] font-medium"
																		>Border</Label
																	>
																	<ColorInput
																		value={def.borderColor || '#e5e7eb'}
																		swatches={tenantPalette}
																		onchange={(v) => (def.borderColor = v)}
																	/>
																</div>
																<div class="space-y-1">
																	<Label class="text-muted-foreground text-[10px] font-medium"
																		>Background</Label
																	>
																	<ColorInput
																		value={def.background || '#ffffff'}
																		swatches={tenantPalette}
																		onchange={(v) => (def.background = v)}
																	/>
																</div>
															</div>
														</div>
													</div>
												{/snippet}

												{#if hfSub === 'header'}
													{#if schema.headers[selectedPageIndex]?.enabled}
														{@render hfPanel(schema.headers[selectedPageIndex], () => {
															schema.headers[selectedPageIndex] = {
																...schema.headers[selectedPageIndex]!,
																enabled: false
															};
														})}
													{:else}
														<div class="flex flex-col items-center gap-3 py-6 text-center">
															<div
																class="bg-muted flex size-10 items-center justify-center rounded-full"
															>
																<PanelTopIcon class="text-muted-foreground size-4" />
															</div>
															<p class="text-muted-foreground text-sm">
																No header on {pageNames[selectedPageIndex] ??
																	`Page ${selectedPageIndex + 1}`}
															</p>
															<Button
																variant="outline"
																size="sm"
																onclick={() =>
																	(schema.headers[selectedPageIndex] = defaultHeader())}
																>+ Add header</Button
															>
														</div>
													{/if}
												{:else if schema.footers[selectedPageIndex]?.enabled}
													{@render hfPanel(schema.footers[selectedPageIndex], () => {
														schema.footers[selectedPageIndex] = {
															...schema.footers[selectedPageIndex]!,
															enabled: false
														};
													})}
												{:else}
													<div class="flex flex-col items-center gap-3 py-6 text-center">
														<div
															class="bg-muted flex size-10 items-center justify-center rounded-full"
														>
															<PanelBottomIcon class="text-muted-foreground size-4" />
														</div>
														<p class="text-muted-foreground text-sm">
															No footer on {pageNames[selectedPageIndex] ??
																`Page ${selectedPageIndex + 1}`}
														</p>
														<Button
															variant="outline"
															size="sm"
															onclick={() => (schema.footers[selectedPageIndex] = defaultFooter())}
															>+ Add footer</Button
														>
													</div>
												{/if}
											</div>
										</InspectorSector>
									{/if}
									<!-- Text table — typed directly on the canvas; add/remove rows and columns here. -->
									{#if isTextTable}
										<InspectorSector label="Text table" class="border-b">
											<div class="space-y-2 p-2.5">
												{#if selectedComp !== textTableComp}
													<Button
														variant="outline"
														size="sm"
														class="w-full"
														onclick={() => selectWholeTable(textTableComp)}
													>
														Select whole table
													</Button>
												{/if}
												<p class="text-muted-foreground text-xs">
													Click a cell to type. Adjust the grid:
												</p>
												<div class="grid grid-cols-2 gap-1.5">
													<Button variant="outline" size="sm" onclick={() => ttCall('addRow')}>
														<PlusIcon class="mr-1 size-3.5" /> Row
													</Button>
													<Button variant="outline" size="sm" onclick={() => ttCall('removeRow')}>
														<Trash2Icon class="mr-1 size-3.5" /> Row
													</Button>
													<Button variant="outline" size="sm" onclick={() => ttCall('addColumn')}>
														<PlusIcon class="mr-1 size-3.5" /> Column
													</Button>
													<Button
														variant="outline"
														size="sm"
														onclick={() => ttCall('removeColumn')}
													>
														<Trash2Icon class="mr-1 size-3.5" /> Column
													</Button>
												</div>
												{#if textTablePreviewHtml}
													<div class="space-y-1.5 pt-1">
														<p
															class="text-muted-foreground text-[10px] font-semibold uppercase tracking-[0.05em]"
														>
															Preview
														</p>
														{@render tableViz(textTablePreviewHtml)}
													</div>
												{/if}
											</div>
										</InspectorSector>
									{/if}
									<!-- Jinja table — edited in a spacious modal (see the config dialog below). -->
									{#if isJtable}
										<div class="space-y-2 border-b p-2.5">
											<Button
												variant="outline"
												size="sm"
												class="w-full"
												onclick={() => (jtableConfigOpen = true)}>Configure table</Button
											>
											{#if jtablePreviewHtml}
												<div class="space-y-1.5">
													<p
														class="text-muted-foreground text-[10px] font-semibold uppercase tracking-[0.05em]"
													>
														Preview
													</p>
													{@render tableViz(jtablePreviewHtml)}
												</div>
											{/if}
										</div>
									{/if}
									<!-- Image settings — bespoke shadcn controls in place of the <image>'s GrapesJS
									     trait widgets (alt text / object-fit / rounded corners). -->
									{#if isImageSelected && !multiSelect}
										<InspectorSector label="Image" bind:open={imageSetupOpen} class="border-b">
											<div class="space-y-3 px-3 pb-3">
												<div class="space-y-1.5">
													<Label
														for="image-alt"
														class="text-muted-foreground text-[11px] font-medium">Alt text</Label
													>
													<Input
														id="image-alt"
														type="text"
														value={imageDraft.alt}
														onchange={(e: Event) =>
															setImageAlt((e.target as HTMLInputElement).value)}
														placeholder="Describe the image"
														class="h-8 px-2 text-sm"
													/>
												</div>
												<div class="space-y-1.5">
													<Label
														for="image-fit"
														class="text-muted-foreground text-[11px] font-medium">Fit</Label
													>
													<SelectMenu
														id="image-fit"
														items={IMAGE_FIT_OPTS.map(([value, label]) => ({ value, label }))}
														value={imageDraft.objectFit}
														onValueChange={setImageFit}
													/>
												</div>
												<div class="space-y-1.5">
													<Label class="text-muted-foreground text-[11px] font-medium"
														>Corners</Label
													>
													<Segmented
														class="w-full"
														options={[
															{ value: 'square', label: 'Square' },
															{ value: 'rounded', label: 'Rounded' }
														]}
														value={imageDraft.rounded ? 'rounded' : 'square'}
														onValueChange={(v) => setImageRounded(v === 'rounded')}
													/>
												</div>
											</div>
										</InspectorSector>
									{/if}
									<!-- Repeat (loop) settings — the loop's collection + the fields it exposes. -->
									{#if isRepeatSelected && !multiSelect}
										<InspectorSector label="Repeat" set={!!repeatDataset} class="border-b">
											<div class="space-y-3 px-3 py-3">
												<div class="space-y-1.5">
													<Label
														for="repeat-dataset"
														class="text-muted-foreground text-[11px] font-medium">Repeat over</Label
													>
													<SelectMenu
														id="repeat-dataset"
														items={collectionItems(repeatDataset, '— Choose a collection —')}
														value={repeatDataset}
														onValueChange={setRepeatDataset}
													/>
												</div>
												{#if repeatDataset && repeatColumns.length}
													<div class="space-y-1.5">
														<p class="text-muted-foreground text-[11px] font-medium">
															Fields available inside the loop
														</p>
														<div class="flex flex-wrap gap-1">
															{#each repeatColumns as col (col)}
																<span
																	class="bg-muted text-foreground/80 rounded px-1.5 py-0.5 font-mono text-[10px]"
																	>row.{col}</span
																>
															{/each}
														</div>
													</div>
												{:else}
													<p class="text-muted-foreground text-[10px] leading-snug">
														{repeatDataset
															? 'Drop content inside the loop and reference fields as row.<field>.'
															: 'Choose a collection to repeat over. Each row renders the content you drop inside.'}
													</p>
												{/if}
											</div>
										</InspectorSector>
									{/if}
									<!-- Conditional settings — build the rule (field/operator/value or a raw expression),
									     the show/hide action, and an optional "otherwise" branch. -->
									{#if isConditionalSelected && !multiSelect}
										<InspectorSector label="Condition" set={!!condExprPreview} class="border-b">
											<div class="space-y-3 px-3 py-3">
												<!-- The simple/advanced switch is in the BODY: `InspectorSector` owns the header
												     row, and a control that changes what the section CONTAINS belongs with the
												     content it changes rather than beside the section's name. -->
												<button
													type="button"
													class="text-muted-foreground hover:text-foreground -mt-1 text-[10px] underline"
													onclick={() => {
														condMode = condMode === 'simple' ? 'advanced' : 'simple';
														applyCondition();
													}}
												>
													{condMode === 'simple' ? 'Advanced' : 'Simple'}
												</button>
												{#if condMode === 'simple'}
													<div class="space-y-1.5">
														<Label class="text-muted-foreground text-[11px] font-medium">When</Label
														>
														<SelectMenu
															items={[
																{ value: '', label: '— Choose a field —' },
																...conditionFields.map((v) => ({
																	value: v.key,
																	label: `${v.label} (${v.key})`
																}))
															]}
															value={condField}
															onValueChange={(v) => {
																condField = v;
																applyCondition();
															}}
														/>
														<SelectMenu
															items={COND_OPS.map((o) => ({ value: o.value, label: o.label }))}
															value={condOp}
															onValueChange={(v) => {
																condOp = v;
																applyCondition();
															}}
														/>
														{#if condNeedsValue}
															<Input
																value={condValue}
																placeholder="Value"
																onchange={(e: Event) => {
																	condValue = (e.target as HTMLInputElement).value;
																	applyCondition();
																}}
																class="h-8 px-2 text-sm"
															/>
														{/if}
													</div>
												{:else}
													<div class="space-y-1.5">
														<Label
															for="cond-raw"
															class="text-muted-foreground text-[11px] font-medium"
															>Expression</Label
														>
														<Input
															id="cond-raw"
															value={condRaw}
															placeholder="e.g. client.age >= 18 and pot.value > 0"
															onchange={(e: Event) => {
																condRaw = (e.target as HTMLInputElement).value;
																applyCondition();
															}}
															class="h-8 px-2 font-mono text-xs"
														/>
														<p class="text-muted-foreground text-[10px] leading-snug">
															A Jinja boolean expression.
														</p>
													</div>
												{/if}
												<div class="space-y-1.5">
													<Label class="text-muted-foreground text-[11px] font-medium">Then</Label>
													<Segmented
														class="w-full"
														options={[
															{ value: 'show', label: 'Show content' },
															{ value: 'hide', label: 'Hide content' }
														]}
														value={condAction}
														onValueChange={(v) => {
															condAction = v as 'show' | 'hide';
															applyCondition();
														}}
													/>
												</div>
												<label class="text-foreground/80 flex items-center gap-2 text-[11px]">
													<Checkbox
														checked={condHasElse}
														onCheckedChange={(v) => setCondHasElse(!!v)}
													/>
													Add an “otherwise” branch
												</label>
												{#if condExprPreview}
													<p
														class="bg-muted text-muted-foreground rounded px-2 py-1 font-mono text-[10px] break-all"
													>
														{'{'}% if {condExprPreview} %{'}'}
													</p>
												{/if}
											</div>
										</InspectorSector>
									{/if}
									<!-- Text styles — one-click named typographic presets for the selected text element. -->
									{#if isTextSelected && !multiSelect}
										<div class="space-y-2 border-b px-3 py-3">
											<Label for="text-style" class="text-muted-foreground text-[11px] font-medium"
												>Text style</Label
											>
											<SelectMenu
												id="text-style"
												items={[
													{ value: '', label: 'Apply a preset…' },
													...TEXT_STYLES.map((s) => ({ value: s.key, label: s.label }))
												]}
												value={textStylePick}
												onValueChange={applyTextStyle}
											/>
											<p class="text-muted-foreground text-[10px] leading-snug">
												Sets a consistent size, weight and spacing — fine-tune below.
											</p>
										</div>
									{/if}
									<!-- GrapesJS traits panel — type-specific, so hidden for jinja-var and multi-select -->
									<div
										id="gjs-traits-panel"
										bind:this={traitsEl}
										class:hidden={!selectedComp ||
											jinjaVarKey !== null ||
											multiSelect ||
											isPageSelected ||
											isImageSelected ||
											isRepeatSelected ||
											isConditionalSelected}
									></div>
									<!-- The class/state control only: GrapesJS's SelectorManager still renders here.
									     Its StyleManager no longer does (`create.ts`: `custom: true`) — the rail
									     below is ours. -->
									<div
										id="gjs-styles-panel"
										bind:this={stylesEl}
										class:hidden={!selectedComp || cssMode || hideStyle}
									></div>
									<!-- Rendered only for a SELECTION. With no target every control falls back to its
									     DEFAULT — numbers that belong to nothing, and writes that go nowhere. -->
									{#if selectedComp && !cssMode && !hideStyle}
										<StylePanel
											styleManager={editor?.StyleManager}
											revision={styleRevision}
											disabled={readOnly}
											onchange={() => {
												// Bump so the panel re-reads what the write actually produced — a
												// composite can normalise a value, and the control must show the
												// stored one rather than the typed one. Then autosave, on the same
												// path as every other edit.
												styleRevision++;
												editorTick++;
												scheduleAutosave();
											}}
										/>
									{/if}
									{#if selectedComp && cssMode && !multiSelect && !hideStyle}
										<div class="space-y-2 p-3">
											<Textarea
												bind:value={cssText}
												onblur={applyCss}
												spellcheck="false"
												placeholder={'color: #1e3a5f;\npadding: 12px;'}
												class="h-48 resize-y p-2 font-mono text-xs"
											/>
											<Button variant="outline" size="sm" class="w-full" onclick={applyCss}
												>Apply CSS</Button
											>
											{#if cssErrors.length}
												<div
													class="border-destructive/30 bg-destructive/5 text-destructive space-y-0.5 rounded-md border p-2 text-[11px]"
												>
													{#each cssErrors as err (err)}
														<p>⚠ {err}</p>
													{/each}
												</div>
											{/if}
										</div>
									{/if}
								</div>
							</div>
						</div>
						<!-- Form + Datasets are managed on the template detail page (Inputs / Datasets tabs);
						     merge-field variables come from the linked form via the `formFields` prop. -->
						<!-- Datasets section (hidden — managed on the template detail page; kept mounted for chart/table binding). -->
						<div class="hidden">
							<button
								type="button"
								onclick={() => (datasetSectionOpen = !datasetSectionOpen)}
								class="text-foreground flex items-center gap-1.5 px-3 py-1.5 text-left"
								aria-expanded={datasetSectionOpen}
							>
								<DatabaseIcon class="size-3.5 shrink-0" />
								<span class="flex-1 text-[11px] font-bold uppercase tracking-wide">Datasets</span>
								<ChevronDownIcon
									class="size-3 shrink-0 transition-transform {datasetSectionOpen
										? ''
										: '-rotate-90'}"
								/>
							</button>
							<div class:hidden={!datasetSectionOpen} class="space-y-2 border-t p-4">
								<p class="text-muted-foreground text-xs leading-relaxed">
									Data sources this template pulls rows from — used in tables, charts and loops.
								</p>
								{#if linkedDatasets.length === 0}
									<p class="text-muted-foreground text-xs">No datasets linked yet.</p>
								{:else}
									{#each linkedDatasets as ds (ds)}
										<div
											class="border-border text-foreground/70 group flex items-center gap-2 rounded-md border px-2 py-1.5 hover:border-brand/40 hover:bg-brand/10 hover:text-foreground"
										>
											<DatabaseIcon class="size-3.5 shrink-0 opacity-60" />
											<span class="flex min-w-0 flex-1 flex-col leading-tight">
												<span class="truncate text-xs" title={ds}
													>{datasetOptions.find((d) => d.code === ds)?.name ?? ds}</span
												>
												<span class="text-muted-foreground truncate font-mono text-[10px]"
													>{ds}</span
												>
											</span>
											<button
												type="button"
												aria-label="Remove {ds}"
												class="hover:text-destructive text-xs opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
												onclick={() => removeLinkedDataset(ds)}>×</button
											>
										</div>
									{/each}
								{/if}
								{#if datasetOptions.filter((d) => !linkedDatasets.includes(d.code)).length}
									<SelectMenu
										items={datasetOptions
											.filter((d) => !linkedDatasets.includes(d.code))
											.map((d) => ({ value: d.code, label: d.name }))}
										bind:value={addDatasetValue}
										onValueChange={addLinkedDataset}
										placeholder="+ Add dataset…"
										size="sm"
									/>
								{:else}
									<p class="text-muted-foreground text-xs">
										{datasetOptions.length
											? 'All available datasets are linked.'
											: 'No datasets available yet — create one from the Datasets page.'}
									</p>
								{/if}
							</div>
						</div>
					</div>
				</aside>
				{#if !railOpen && !previewMode}
					<!-- Shut, the rail is one arrow tab near the canvas's right edge (`email-editor.svelte`'s
					     `.rail-reopen`), so the way back is where the rail was. Hidden in preview, where
					     there is nothing to style and the tab would only offer to cover the page. This one
					     holds no GrapesJS container, so unlike the rail it is safe to destroy.
					     `right-8` CLEARS THE PAGE'S OWN SCROLLBAR and is not spacing: the frame is full
					     width and the page is centred INSIDE it (`grapesjs-canvas.css` centres with
					     `text-align`, not flex), so the iframe's scrollbar sits at the frame's right edge —
					     one `.gjs-cv-canvas` padding in, 16px. Flush at `right-0` the tab covered it. 32px
					     is that 16px plus the widest that bar gets (5px on WebKit, ~11px for Firefox's
					     `scrollbar-width: thin`) plus air, so it clears on both engines. Fully rounded and
					     fully bordered BECAUSE it is inset: the half-tab treatment only reads as pulled out
					     of an edge when it is touching one. -->
					<button
						type="button"
						class="text-muted-foreground hover:text-foreground border-border absolute right-8 top-3 z-20 flex h-8 w-7 cursor-pointer items-center justify-center rounded-md border bg-[color-mix(in_oklab,var(--card)_97%,var(--foreground))] shadow-[var(--elev-1)] transition-colors"
						title="Show the style panel"
						aria-label="Show the style panel"
						onclick={() => (railOpen = true)}
					>
						<ChevronLeftIcon class="size-4" />
					</button>
				{/if}
				{#if codeOpen}
					<!-- OVER the canvas (the `initError` posture), not beside it: the pane is the same
					     document, so putting it alongside would ask which of two pictures of one thing is
					     the real one. `z-30` clears the canvas chrome; the canvas keeps rendering behind,
					     so closing is instant and nothing is re-initialised. -->
					<div class="absolute inset-0 z-30">
						<CodeView
							html={codeHtml}
							shape={codeShape}
							onshapechange={(next) => (codeShape = next)}
							shapeLabels={{ source: 'Editor', output: 'Template' }}
							shapeNotes={{
								source:
									'What the visual editor holds, and the only shape a hand edit can be applied to. The amber attributes are the editor’s own — the panel edits this document’s conditions, loops, variables and charts through them.',
								output:
									'The Jinja template the renderer is handed: those attributes compiled away, page geometry baked in. Derived from the canvas, so there is nothing to edit here — switch to Editor for the shape an edit can be written back to.'
							}}
							markerAttrs={EDITOR_ATTRS}
							onapply={readOnly ? undefined : applyHandEditedHtml}
							onclose={() => (codeOpen = false)}
						/>
					</div>
				{/if}
				{#if initError}
					<!-- Over the canvas, not a toast: the failure IS the empty canvas, so the explanation
					     belongs where the reader is already looking, and it must not be dismissable into
					     the same silence it replaced. -->
					<div class="bg-muted/40 absolute inset-0 z-30 flex items-center justify-center p-6">
						<div class="max-w-sm text-center">
							<TriangleAlertIcon class="text-warning mx-auto size-8" />
							<p class="text-foreground mt-3 text-sm font-medium">{initError}</p>
							<p class="text-muted-foreground mt-1 text-sm">
								Nothing has been lost — your saved content is still on the template. The browser
								console has the details.
							</p>
							<Button variant="outline" size="sm" class="mt-4" onclick={() => location.reload()}>
								Reload the editor
							</Button>
						</div>
					</div>
				{/if}
				{#if canvasEmpty && !previewMode && !readOnly && !emptyStateDismissed}
					<!-- Dismissible starting hint — a small popup near the top of the canvas (not a pinned
					     full-canvas overlay), with a close button. The palette is one click away, so it never
					     blocks and can be dismissed. pointer-events-none on the wrapper lets drags reach the
					     page; only the popup is interactive. -->
					<div class="pointer-events-none absolute inset-x-0 top-4 z-10 flex justify-center px-6">
						<div
							class="pointer-events-auto bg-card ring-foreground/10 relative flex max-w-md items-center gap-3 rounded-xl border py-2.5 pl-3.5 pr-9 shadow-lg ring-1"
						>
							<div
								class="bg-muted text-muted-foreground flex size-8 shrink-0 items-center justify-center rounded-full"
							>
								<BoxIcon class="size-4" />
							</div>
							<div class="min-w-0 text-left">
								<p class="text-foreground text-xs font-semibold">Start your document</p>
								<p class="text-muted-foreground text-[11px] leading-snug">
									Add content from the palette.
								</p>
							</div>
							<div class="flex shrink-0 items-center gap-1.5">
								<Button size="sm" variant="brand" class="h-7" onclick={() => (leftView = 'blocks')}>
									<PlusIcon class="size-3.5" /> Open blocks
								</Button>
							</div>
							<button
								type="button"
								onclick={dismissEmptyHint}
								aria-label="Dismiss"
								title="Dismiss"
								class="text-muted-foreground hover:text-foreground hover:bg-muted absolute right-1.5 top-1.5 rounded p-0.5 transition-colors"
							>
								<XIcon class="size-3.5" />
							</button>
						</div>
					</div>
				{/if}
				{#if previewMode}
					<!-- Data-filled sample preview from the server, laid over the canvas. -->
					<div class="absolute inset-y-0 left-0 right-8 z-20 flex flex-col bg-muted">
						{#if docPreviewUnknowns.length && !previewWarningDismissed}
							<div
								class="border-warning/30 bg-warning/10 text-warning relative flex shrink-0 flex-wrap items-center gap-x-1 gap-y-0.5 border-b px-4 py-2 pr-9 text-xs"
							>
								<span class="font-medium">
									⚠ {docPreviewUnknowns.length} unknown variable{docPreviewUnknowns.length === 1
										? ''
										: 's'} won't fill:
								</span>
								{#each docPreviewUnknowns as u (u.name)}
									<span class="inline-flex items-center gap-1">
										<code class="bg-warning/10 rounded px-1 font-mono">{`{{ ${u.name} }}`}</code>
										{#if u.suggestion}<span class="text-warning/70">→ {u.suggestion}?</span>{/if}
									</span>
								{/each}
								<span class="text-warning/80"
									>— pick fields from the variable list instead of typing them.</span
								>
								<button
									type="button"
									onclick={() => (previewWarningDismissed = true)}
									aria-label="Dismiss warning"
									title="Dismiss"
									class="text-warning/70 hover:text-warning hover:bg-warning/10 absolute right-2 top-1.5 rounded p-1 leading-none transition-colors"
								>
									<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"
										><path
											d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06z"
										/></svg
									>
								</button>
							</div>
						{/if}
						<!-- Scroll container. `text-center` + an inline-block child centres the sheet at any
						     zoom. A soft neutral surround (not the flat grey it was) with the document framed
						     as a white sheet reads as a real page. The rendered preview stays mounted while a
						     re-render runs (spinner overlay below) so it never flashes blank. -->
						<div class="bg-background relative min-h-0 flex-1 overflow-auto p-6 text-center">
							{#if docPreviewError}
								<div class="flex h-full items-center justify-center">
									<p class="text-muted-foreground max-w-sm text-center text-sm">
										{docPreviewError}
									</p>
								</div>
							{:else if docPreviewPdfUrl}
								<!-- Self-rendered (pdf.js): clean stacked sheets filling the pane — no native
								     PDF-viewer chrome, no grey surround, no plugin scrollbar. Reads like the canvas. -->
								<PdfViewer src={docPreviewPdfUrl} class="absolute inset-0" />
							{:else if docPreviewHtml}
								<!-- Fallback (the PDF render engine is unavailable, e.g. local dev): the fast HTML
								     layout preview. CSS `zoom` (shared with the canvas) scales the wrapper; the
								     iframe is sized to its content so there's no inner scrollbar. -->
								<div class="inline-block align-top" style="zoom: {zoom / 100}">
									<!-- `allow-same-origin` and NOT `allow-scripts`: the sandbox blocks script
									     execution either way, and keeping the origin is what lets
									     `sizePreviewFrame` read `contentDocument` to size the frame to its
									     content. The two together would be equivalent to no sandbox at all;
									     this pairing is the one that measures AND refuses to execute. -->
									<iframe
										bind:this={previewFrameEl}
										onload={sizePreviewFrame}
										title="Document preview"
										srcdoc={docPreviewHtml}
										sandbox="allow-same-origin"
										class="ring-foreground/10 block rounded-lg shadow-sm ring-1"
										style="width: {previewFrameWidth}px; height: {previewFrameHeight}px; background: #ffffff"
									></iframe>
								</div>
							{:else if !docPreviewLoading}
								<div class="flex h-full items-center justify-center">
									<p class="text-muted-foreground text-sm">Nothing to preview yet.</p>
								</div>
							{/if}
							<!-- (Re)generating: a spinner over the previous preview so it never flashes blank. -->
							{#if docPreviewLoading}
								<div
									class="bg-background/40 absolute inset-0 z-10 flex items-start justify-center pt-24 backdrop-blur-[1px]"
								>
									<div
										class="bg-card ring-foreground/10 flex items-center gap-2 rounded-full px-3.5 py-2 text-xs font-medium shadow-sm ring-1"
									>
										<svg class="text-brand size-4 animate-spin" viewBox="0 0 24 24" fill="none">
											<circle
												class="opacity-25"
												cx="12"
												cy="12"
												r="10"
												stroke="currentColor"
												stroke-width="4"
											/>
											<path
												class="opacity-75"
												fill="currentColor"
												d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z"
											/>
										</svg>
										Rendering preview…
									</div>
								</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>

<!-- Chart / Table config modal -->
{#if chartConfigOpen}
	<div class="fixed inset-0 z-50 flex items-center justify-center">
		<!-- Backdrop. Derived from the ink token rather than a literal black wash: a scrim has to stay DARK in both
		     schemes (a themed `bg-foreground/40` inverts to a white wash in dark mode and destroys the
		     sense of modality), while still picking up the firm's ink hue when it re-themes. -->
		<button
			type="button"
			class="absolute inset-0 bg-[color-mix(in_oklab,var(--hero-base)_55%,transparent)]"
			aria-label="Close"
			onclick={() => (chartConfigOpen = false)}
		></button>

		<!-- Dialog. WIDE, because this is a working surface and not a confirmation: at 420px the chart
		     type strip clipped its own last option, every field sat in one long scroll, and the preview
		     the dialog exists for was off screen by the time you reached the bindings it reflects.
		     `min(1080px, 94vw)` so it is still a dialog on a laptop and still fits a narrow window. -->
		<div
			class="bg-background relative z-10 flex max-h-[90svh] w-[min(1080px,94vw)] flex-col overflow-hidden rounded-xl border shadow-2xl"
		>
			<!-- Header -->
			<div class="flex items-center justify-between border-b px-5 py-4">
				<h2 class="text-sm font-semibold">Configure {draftIsTable ? 'Table' : 'Chart'}</h2>
				<button
					type="button"
					class="text-muted-foreground hover:text-foreground"
					onclick={() => (chartConfigOpen = false)}
					aria-label="Close"
				>
					<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"
						><path
							d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06z"
						/></svg
					>
				</button>
			</div>

			<!-- TWO COLUMNS, and the preview holds its own. It is the reason this dialog exists, and in
			     a single column it scrolled out of sight exactly when it was needed — you changed a
			     binding and could not see what it did without scrolling back up. Each column scrolls
			     separately. STACKED below `lg`, where side by side would leave neither usable. -->
			<div class="flex min-h-0 flex-1 flex-col lg:flex-row">
				<div
					class="min-h-0 shrink-0 overflow-y-auto border-b p-5 lg:w-[440px] lg:border-b-0 lg:border-r"
				>
					<!-- Live preview — resolves the dataset's REAL rows, so a blank preview means the data
					     isn't linking up, not a rendering bug. Charts AND tables: a static picture of the
					     chosen type answers nothing an author is in this dialog to ask. -->
					<div class="space-y-2">
						<div class="flex items-center justify-between gap-2">
							<p class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide">
								Preview
							</p>
							{#if previewDataset?.scope === 'subject' && previewClients.length}
								<SelectMenu
									items={[
										{ value: '', label: 'Sample client…' },
										...previewClients.map((c) => ({
											value: String(c.contactId),
											label: c.name
										}))
									]}
									value={previewContactId != null ? String(previewContactId) : ''}
									onValueChange={(v) => (previewContactId = v ? Number(v) : null)}
									size="sm"
									class="w-auto"
								/>
							{/if}
						</div>
						<div
							class="bg-doc-paper flex min-h-[180px] items-center justify-center overflow-hidden rounded-lg border p-2"
						>
							{#if previewLoading}
								<p class="text-muted-foreground text-xs">Resolving dataset…</p>
							{:else if !chartDraft['data-comp-data-source'] && chartDraft['data-comp-source-mode'] !== 'manual'}
								<p class="text-muted-foreground text-xs">
									Choose a data source to preview the {draftIsTable ? 'table' : 'chart'}.
								</p>
							{:else if draftIsTable && !previewTableHtml}
								<p class="text-muted-foreground text-xs">Choose columns to preview the table.</p>
							{:else if draftIsTable}
								<!-- eslint-disable-next-line svelte/no-at-html-tags -- tablePreviewInner output; every cell is HTML-escaped -->
								{@html previewTableHtml}
							{:else}
								<!-- eslint-disable-next-line svelte/no-at-html-tags -- our own renderChartSvg output; every value is HTML-escaped -->
								{@html previewSvg}
							{/if}
						</div>
						<!-- ONE LINE, not two. A red "Dataset not found" stacked above an amber "showing sample
						     values" over a drawn chart reads as a contradiction — it looks like the panel both
						     failed and succeeded. Sampling and the reason for it are one fact, so they are one
						     sentence, and the reason is never dropped: a sample picture must not be the only
						     answer a broken binding gets. -->
						{#if previewSampled}
							<p class="text-warning text-[11px]">
								Showing sample values — {previewError
									? previewError.replace(/\.$/, '')
									: 'this dataset resolved no rows'}. This is the layout, not this client's data.
							</p>
						{:else if previewError}
							<p class="text-destructive text-[11px]">{previewError}</p>
						{/if}
						{#if chartDraft['data-comp-data-source'] || chartDraft['data-comp-source-mode'] === 'manual'}
							<p class="text-muted-foreground text-[11px] leading-relaxed">
								{previewRows.length} row{previewRows.length === 1 ? '' : 's'} resolved{previewDataset
									?.columns.length
									? ` · columns: ${previewDataset.columns.join(', ')}`
									: ''}
							</p>
							{#if previewMissingFields.length}
								<p class="text-warning text-[11px]">
									⚠ Not in this dataset: {previewMissingFields.join(', ')}
								</p>
							{/if}
							{#if !previewLoading && !previewError && previewRows.length === 0}
								<p class="text-warning text-[11px] leading-relaxed">
									Resolved 0 rows{previewDataset?.scope === 'subject'
										? ' — pick a client above, or check that client is linked to this data'
										: ''}. This is why the chart is blank in the PDF.
								</p>
							{/if}
						{/if}
					</div>
				</div>

				<!-- The settings column: everything the preview beside it answers. -->
				<div class="min-h-0 flex-1 overflow-y-auto">
					<div class="space-y-5 p-5">
						<!-- DATA AND APPEARANCE ARE DIFFERENT JOBS, and they were one scroll: the appearance
					     controls sat below a hundred-odd field pills, so a firm adjusting its colours
					     scrolled past every binding to reach them. `data` leads because a chart with no
					     binding has nothing to appear as. -->
						<Segmented
							class="w-full"
							options={[
								{ value: 'data', label: 'Data' },
								{ value: 'appearance', label: 'Appearance' }
							]}
							value={configTab}
							onValueChange={(v) => (configTab = v as 'data' | 'appearance')}
						/>
						{#if configTab === 'data'}
							<!-- Chart type (charts only) -->
							{#if !draftIsTable}
								<div class="space-y-2">
									<p
										class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
									>
										Chart type
									</p>
									<Segmented
										class="w-full"
										options={CHART_TYPES.map(([value, label]) => ({ value, label }))}
										value={draftType}
										onValueChange={(v) => (chartDraft['data-comp-type'] = v)}
									/>
								</div>
							{/if}
							<!-- Data source -->
							<div class="space-y-1.5">
								<label
									for="chart-data-source"
									class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
									>Data source</label
								>
								<Segmented
									class="w-full"
									options={[
										{ value: 'dataset', label: 'Dataset' },
										{ value: 'manual', label: 'Manual' }
									]}
									value={chartDraft['data-comp-source-mode'] ?? 'dataset'}
									onValueChange={setChartSourceMode}
								/>
								{#if (chartDraft['data-comp-source-mode'] ?? 'dataset') === 'manual'}
									<p class="text-muted-foreground text-[11px] leading-relaxed">
										Type the data below (or import a CSV). It's stored on this component and renders
										as-is — no dataset needed.
									</p>
									<DatasetDataGrid
										bind:columns={manualColumns}
										bind:rows={manualRows}
										id="chart-manual-grid"
									/>
								{:else}
									<!-- A real picker over the tenant's datasets (was free text that resolved nothing).
						     A stored value that no longer matches a dataset stays selectable, flagged
						     unknown, so old templates surface the problem instead of silently losing it. -->
									<SelectMenu
										id="chart-data-source"
										items={collectionItems(
											chartDraft['data-comp-data-source'] ?? '',
											'— Choose a dataset —'
										)}
										value={chartDraft['data-comp-data-source']}
										onValueChange={(v) => (chartDraft['data-comp-data-source'] = v)}
									/>
								{/if}
							</div>

							<!-- Fields (pill selectors from dataset schema) -->
							{#if draftIsTable}
								<div class="space-y-3">
									<div class="space-y-2">
										<p
											class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
										>
											Columns
										</p>
										{#if draftColumns.length}
											<div class="flex flex-wrap gap-1.5">
												{#each draftColumns as col (col)}
													<span
														class="inline-flex items-center gap-1 rounded-full border border-foreground/20 bg-foreground/10 px-2.5 py-0.5 text-xs font-medium"
													>
														{col}<button
															type="button"
															onclick={() => removeColumn(col)}
															class="text-foreground/50 hover:text-foreground leading-none"
															aria-label="Remove {col}">×</button
														>
													</span>
												{/each}
											</div>
										{/if}
										{#if availableCols.length}
											<div class="flex flex-wrap gap-1.5">
												{#each availableCols.filter((c) => !draftColumns.includes(c)) as col (col)}
													<button
														type="button"
														onclick={() => addColumn(col)}
														class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors border-border text-muted-foreground hover:border-foreground/40 hover:text-foreground"
														>+ {col}</button
													>
												{/each}
											</div>
										{:else}
											<p class="text-muted-foreground text-[10px]">
												{chartDraft['data-comp-data-source']
													? 'Schema unknown — enter columns manually below.'
													: 'Enter a data source to see available columns.'}
											</p>
										{/if}
									</div>
									<div class="space-y-1.5">
										<label
											for="chart-headers"
											class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
											>Column headers</label
										>
										<input
											id="chart-headers"
											value={chartDraft['data-comp-headers']}
											placeholder="Fund,Allocation,Value"
											oninput={(e: Event) =>
												(chartDraft['data-comp-headers'] = (e.target as HTMLInputElement).value)}
											class="border-input focus:border-brand h-9 w-full rounded-md border bg-transparent px-3 text-sm outline-none"
										/>
									</div>
								</div>
							{:else if draftIsPie}
								<div class="space-y-3">
									<div class="space-y-2">
										<p
											class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
										>
											Label field
										</p>
										{#if availableCols.length}
											<div class="bg-muted flex flex-wrap rounded-lg p-0.5">
												{#each availableCols as col (col)}
													<button
														type="button"
														onclick={() => setLabelField(col)}
														class="rounded-md px-2.5 py-1.5 text-xs font-medium transition-all {chartDraft[
															'data-comp-label-field'
														] === col
															? 'bg-brand text-brand-foreground shadow-sm'
															: 'text-muted-foreground hover:text-foreground'}">{col}</button
													>
												{/each}
											</div>
										{:else}
											<p class="text-muted-foreground text-[10px]">Enter a data source above.</p>
										{/if}
									</div>
									<div class="space-y-2">
										<p
											class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
										>
											Value field
										</p>
										{#if availableCols.length}
											<div class="bg-muted flex flex-wrap rounded-lg p-0.5">
												{#each availableCols as col (col)}
													<button
														type="button"
														onclick={() => setValueField(col)}
														class="rounded-md px-2.5 py-1.5 text-xs font-medium transition-all {chartDraft[
															'data-comp-value-field'
														] === col
															? 'bg-brand text-brand-foreground shadow-sm'
															: 'text-muted-foreground hover:text-foreground'}">{col}</button
													>
												{/each}
											</div>
										{:else}
											<p class="text-muted-foreground text-[10px]">Enter a data source above.</p>
										{/if}
									</div>
								</div>
							{:else}
								<div class="space-y-3">
									<div class="space-y-2">
										<p
											class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
										>
											X field
										</p>
										{#if availableCols.length}
											<div class="bg-muted flex flex-wrap rounded-lg p-0.5">
												{#each availableCols as col (col)}
													<button
														type="button"
														onclick={() => setXField(col)}
														class="rounded-md px-2.5 py-1.5 text-xs font-medium transition-all {chartDraft[
															'data-comp-x-field'
														] === col
															? 'bg-brand text-brand-foreground shadow-sm'
															: 'text-muted-foreground hover:text-foreground'}">{col}</button
													>
												{/each}
											</div>
										{:else}
											<p class="text-muted-foreground text-[10px]">
												Enter a data source above to see fields.
											</p>
										{/if}
									</div>
									<div class="space-y-2">
										<p
											class="text-muted-foreground text-[10px] font-semibold uppercase tracking-wide"
										>
											Y field{draftType === 'stacked_area_chart' ? 's' : ''}
										</p>
										{#if draftYFields.length}
											<div class="flex flex-wrap gap-1.5">
												{#each draftYFields as f (f)}
													<span
														class="inline-flex items-center gap-1 rounded-full border border-foreground/20 bg-foreground/10 px-2.5 py-0.5 text-xs font-medium"
													>
														{f}<button
															type="button"
															onclick={() => removeYField(f)}
															class="text-foreground/50 hover:text-foreground leading-none"
															aria-label="Remove {f}">×</button
														>
													</span>
												{/each}
											</div>
										{/if}
										<div class="flex flex-wrap gap-1.5">
											{#each availableCols.filter((c) => c !== chartDraft['data-comp-x-field'] && !draftYFields.includes(c)) as col (col)}
												<button
													type="button"
													onclick={() => addYField(col)}
													class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors border-border text-muted-foreground hover:border-foreground/40 hover:text-foreground"
													>+ {col}</button
												>
											{/each}
											{#if !availableCols.length}
												<p class="text-muted-foreground text-[10px]">
													Enter a data source above to see fields.
												</p>
											{/if}
										</div>
									</div>
								</div>
							{/if}
						{:else}
							<!-- Title + axis labels. APPEARANCE, not data: they are what the chart reads as, and
							     nothing about them is bound to anything. -->
							<!-- THE TWO AXIS LABELS ARE A PAIR and sit as one. Title used to share a row with X
							     while Y ran the full width below it, which paired the two fields that have
							     nothing to do with each other and split the two that answer the same question. -->
							<div class="space-y-3">
								<div class="space-y-1.5">
									<label
										for="chart-title"
										class="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase"
										>Title</label
									>
									<input
										id="chart-title"
										value={chartDraft['data-comp-title']}
										placeholder="Chart title"
										oninput={(e: Event) =>
											(chartDraft['data-comp-title'] = (e.target as HTMLInputElement).value)}
										class="border-input focus:border-brand h-9 w-full rounded-md border bg-transparent px-3 text-sm outline-none"
									/>
								</div>
								{#if !draftIsTable && !draftIsPie}
									<div class="grid grid-cols-2 gap-3">
										<div class="space-y-1.5">
											<label
												for="chart-x-label"
												class="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase"
												>X label</label
											>
											<input
												id="chart-x-label"
												value={chartDraft['data-comp-x-label']}
												placeholder="Year"
												oninput={(e: Event) =>
													(chartDraft['data-comp-x-label'] = (e.target as HTMLInputElement).value)}
												class="border-input focus:border-brand h-9 w-full rounded-md border bg-transparent px-3 text-sm outline-none"
											/>
										</div>
										<div class="space-y-1.5">
											<label
												for="chart-y-label"
												class="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase"
												>Y label</label
											>
											<input
												id="chart-y-label"
												value={chartDraft['data-comp-y-label']}
												placeholder="Fund Value (£)"
												oninput={(e: Event) =>
													(chartDraft['data-comp-y-label'] = (e.target as HTMLInputElement).value)}
												class="border-input focus:border-brand h-9 w-full rounded-md border bg-transparent px-3 text-sm outline-none"
											/>
										</div>
									</div>
								{/if}
							</div>

							<!-- Appearance -->
							{#if draftIsTable}
								<div class="space-y-3">
									<!-- A HEADING, not another row. At muted 10px caps these sat at the same tone and nearly
					     the same size as the labels beneath them, so a group of controls started without
					     anything saying so. Darker ink, a rule across the panel, and space above it. -->
									<p
										class="text-foreground border-border mt-1 border-t pt-3 text-[11px] font-bold tracking-[0.08em] uppercase"
									>
										Appearance
									</p>
									<div class="grid grid-cols-2 gap-3">
										<div class="space-y-1.5">
											<span class="text-muted-foreground text-[10px] font-medium"
												>Header background</span
											>
											<div class="flex items-center gap-2">
												<ColorInput
													value={chartDraft['data-comp-header-bg'] ?? '#1e3a5f'}
													swatches={tenantPalette}
													class="max-w-40"
													oninput={(v) => (chartDraft['data-comp-header-bg'] = v)}
													onchange={(v) => (chartDraft['data-comp-header-bg'] = v)}
												/>
												<span class="text-muted-foreground font-mono text-[10px]"
													>{chartDraft['data-comp-header-bg'] ?? '#1e3a5f'}</span
												>
											</div>
										</div>
										<div class="space-y-1.5">
											<span class="text-muted-foreground text-[10px] font-medium">Header text</span>
											<div class="flex items-center gap-2">
												<ColorInput
													value={chartDraft['data-comp-header-fg'] ?? '#ffffff'}
													swatches={tenantPalette}
													class="max-w-40"
													oninput={(v) => (chartDraft['data-comp-header-fg'] = v)}
													onchange={(v) => (chartDraft['data-comp-header-fg'] = v)}
												/>
												<span class="text-muted-foreground font-mono text-[10px]"
													>{chartDraft['data-comp-header-fg'] ?? '#ffffff'}</span
												>
											</div>
										</div>
									</div>
									<div class="flex items-center justify-between">
										<span class="text-muted-foreground text-xs">Striped rows</span>
										<Segmented
											options={[
												{ value: 'true', label: 'On' },
												{ value: 'false', label: 'Off' }
											]}
											value={chartDraft['data-comp-striped'] ?? 'true'}
											onValueChange={(v) => (chartDraft['data-comp-striped'] = v)}
										/>
									</div>

									<!-- PER-COLUMN LAYOUT. A table could only be as wide as its box and shared that
									     width equally by content, so a reference column took as much room as a
									     description and a column of figures could not be lined up on its digits.
									     Width is a PERCENTAGE: the table is 100% of whatever box it sits in, and a
									     page's content width is a physical measurement an author cannot see from
									     here — a percentage means the same thing at any page size and can never push
									     the table off the sheet. Blank is automatic, and a table with no widths at
									     all keeps content-sized columns rather than being forced to a fixed layout. -->
									{#if draftColumns.length}
										<div class="space-y-2">
											<span class="text-muted-foreground text-[10px] font-medium">Columns</span>
											{#each draftColumns as col, i (col + i)}
												<div class="flex items-center gap-2">
													<span class="text-muted-foreground min-w-0 flex-1 truncate text-xs">
														{(chartDraft['data-comp-headers'] ?? '').split(',')[i]?.trim() || col}
													</span>
													<input
														type="number"
														min="1"
														max="100"
														placeholder="auto"
														value={columnPart('widths', i)}
														oninput={(e: Event) =>
															setColumnPart('widths', i, (e.target as HTMLInputElement).value)}
														class="border-input h-7 w-16 shrink-0 rounded-md border bg-transparent px-2 text-center text-xs outline-none"
														aria-label="Width of {col} as a percentage"
													/>
													<span class="text-muted-foreground shrink-0 text-[10px]">%</span>
													<Segmented
														class="shrink-0"
														options={[
															{ value: 'left', label: 'L' },
															{ value: 'center', label: 'C' },
															{ value: 'right', label: 'R' }
														]}
														value={columnPart('aligns', i) || 'left'}
														onValueChange={(v) => setColumnPart('aligns', i, v)}
													/>
												</div>
											{/each}
										</div>
									{/if}
								</div>
							{:else}
								<div class="space-y-3">
									<!-- A HEADING, not another row. At muted 10px caps these sat at the same tone and nearly
					     the same size as the labels beneath them, so a group of controls started without
					     anything saying so. Darker ink, a rule across the panel, and space above it. -->
									<p
										class="text-foreground border-border mt-1 border-t pt-3 text-[11px] font-bold tracking-[0.08em] uppercase"
									>
										Appearance
									</p>
									<!-- THE SWATCHES WERE A DISPLAY, NOT A CONTROL. This row printed the firm's own
									     colours beside the words "Tenant colours" and could not be clicked, so the one
									     thing an author most often wants from a chart — this line is not the right
									     colour — had no answer anywhere in the editor. Each slot is now the real
									     picker, labelled with the series it paints, and the firm's palette is what it
									     offers as swatches rather than something to look at. -->
									<div class="space-y-1.5">
										<div class="flex items-center justify-between gap-2">
											<span class="text-muted-foreground text-xs">
												{draftIsPie ? 'Slice colours' : 'Series colours'}
											</span>
											<Button
												size="sm"
												variant="ghost"
												class="h-6 px-2 text-[10px]"
												onclick={() => (chartDraft['data-comp-colors'] = tenantPalette.join(','))}
											>
												Reset to tenant colours
											</Button>
										</div>
										<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
											{#each draftColourSlots as slot, i (slot + i)}
												<div class="flex min-w-0 items-center gap-1.5">
													<!-- BOTH CHANNELS. `ColorInput` sends `oninput` for every partial value — each
													     keystroke in the hex field, each move of a pad drag — and `onchange` only
													     at the end of a gesture. With `onchange` alone a typed colour updated the
													     control's own `$bindable` and never reached the draft, so the field showed
													     the new colour while the preview beside it kept drawing the old one. The
													     draft is scratch state and the preview is the reason this drawer exists,
													     so it wants the live value; the document is written on Apply. -->
													<ColorInput
														value={draftPalette[i] ?? ''}
														swatches={tenantPalette}
														class="max-w-40"
														oninput={(v) => setDraftColour(i, v)}
														onchange={(v) => setDraftColour(i, v)}
													/>
													<span
														class="text-muted-foreground max-w-32 truncate text-[10px]"
														title={slot}>{slot}</span
													>
												</div>
											{/each}
										</div>
									</div>
									<!-- TWO TO A ROW. Each of these is a short label and a small control, and as
									     full-width rows they left a hand's width of nothing between the two and ran
									     the panel far enough down that Typography fell below the fold. -->
									<div class="grid grid-cols-2 gap-x-4 gap-y-2">
										<!-- THE CHART'S OWN BACKGROUND, not the frame's. It used to be a Background
										     sector on the wrapper div, two panels away from the chart it paints and
										     layered behind an SVG that drew `none` over it on the promise the div would
										     show through. Here it is a property of the figure, in the row of colours it
										     sits behind. -->
										<div class="flex min-w-0 items-center gap-2">
											<span class="text-muted-foreground w-24 shrink-0 text-xs">Background</span>
											<ColorInput
												value={chartDraft['data-comp-background'] || '#ffffff'}
												swatches={tenantPalette}
												class="min-w-0 flex-1"
												oninput={(v) => (chartDraft['data-comp-background'] = v)}
												onchange={(v) => (chartDraft['data-comp-background'] = v)}
											/>
											<!-- Transparent is the DEFAULT, and `ColorInput` has no way to say it — every
											     colour it can hold is a paint. Without this an author who opened the picker
											     could never get the chart back to sitting on the page. -->
											<Button
												size="sm"
												variant="ghost"
												class="h-7 shrink-0 px-1.5 text-[10px]"
												disabled={!chartDraft['data-comp-background']}
												onclick={() => (chartDraft['data-comp-background'] = '')}
											>
												None
											</Button>
										</div>
										<!-- HOW A FIGURE READS. A financial document's y axis is money far more often
										     than it is a bare count, and there was no way to say so — every chart printed
										     `40,000` where the firm meant `£40,000`. Ticks and data labels share the one
										     setting, because two figures for the same quantity in one picture is worse
										     than either. -->
										<div class="flex min-w-0 items-center gap-2">
											<span class="text-muted-foreground w-24 shrink-0 text-xs">Figures</span>
											<Segmented
												options={[
													{ value: 'number', label: '1,234' },
													{ value: 'currency', label: '£' },
													{ value: 'percent', label: '%' }
												]}
												value={chartDraft['data-comp-value-format'] ?? 'number'}
												onValueChange={(v) => (chartDraft['data-comp-value-format'] = v)}
											/>
										</div>
										<div class="flex min-w-0 items-center gap-2">
											<span class="text-muted-foreground w-24 shrink-0 text-xs">Decimals</span>
											<Segmented
												options={[
													{ value: '', label: 'Auto' },
													{ value: '0', label: '0' },
													{ value: '1', label: '1' },
													{ value: '2', label: '2' }
												]}
												value={chartDraft['data-comp-value-decimals'] ?? ''}
												onValueChange={(v) => (chartDraft['data-comp-value-decimals'] = v)}
											/>
										</div>
										{#if draftType === 'bar_chart' || draftType === 'line_chart'}
											<!-- Bars and line points only. A figure inside a stacked band is unreadable
											     and a pie states its shares in the legend, so the control is absent
											     rather than present and ignored. -->
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Value labels</span
												>
												<Segmented
													options={[
														{ value: 'true', label: 'On' },
														{ value: 'false', label: 'Off' }
													]}
													value={chartDraft['data-comp-show-values'] ?? 'false'}
													onValueChange={(v) => (chartDraft['data-comp-show-values'] = v)}
												/>
											</div>
										{/if}
										<div class="flex min-w-0 items-center gap-2">
											<span class="text-muted-foreground w-24 shrink-0 text-xs">Grid lines</span>
											<Segmented
												options={[
													{ value: 'true', label: 'On' },
													{ value: 'false', label: 'Off' }
												]}
												value={chartDraft['data-comp-show-grid'] ?? 'true'}
												onValueChange={(v) => (chartDraft['data-comp-show-grid'] = v)}
											/>
										</div>
										<div class="flex min-w-0 items-center gap-2">
											<span class="text-muted-foreground w-24 shrink-0 text-xs">Grid colour</span>
											<ColorInput
												value={chartDraft['data-comp-grid-color'] || '#eef2f6'}
												swatches={tenantPalette}
												class="min-w-0 flex-1"
												oninput={(v) => (chartDraft['data-comp-grid-color'] = v)}
												onchange={(v) => (chartDraft['data-comp-grid-color'] = v)}
											/>
										</div>
										<div class="flex min-w-0 items-center gap-2">
											<span class="text-muted-foreground w-24 shrink-0 text-xs">Legend</span>
											<Segmented
												options={[
													{ value: 'true', label: 'On' },
													{ value: 'false', label: 'Off' }
												]}
												value={chartDraft['data-comp-show-legend'] ?? 'true'}
												onValueChange={(v) => (chartDraft['data-comp-show-legend'] = v)}
											/>
										</div>
										{#if draftType === 'line_chart'}
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Line width</span>
												<input
													type="number"
													min="0.5"
													max="5"
													step="0.5"
													value={chartDraft['data-comp-line-width'] ?? '1.5'}
													oninput={(e: Event) =>
														(chartDraft['data-comp-line-width'] = (
															e.target as HTMLInputElement
														).value)}
													class="border-input h-7 w-14 rounded-md border bg-transparent px-2 text-center text-xs outline-none"
												/>
											</div>
											<!-- SHAPE, PER TYPE. Only the controls that mean something for the chart in
											     front of you: a dashed bar and a donut's line width are not questions, and
											     a control that is present and ignored is worse than one that is absent. -->
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Line style</span>
												<Segmented
													options={[
														{ value: 'solid', label: 'Solid' },
														{ value: 'dashed', label: 'Dashed' },
														{ value: 'dotted', label: 'Dotted' }
													]}
													value={chartDraft['data-comp-line-style'] || 'solid'}
													onValueChange={(v) => (chartDraft['data-comp-line-style'] = v)}
												/>
											</div>
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Points</span>
												<Segmented
													options={[
														{ value: 'true', label: 'On' },
														{ value: 'false', label: 'Off' }
													]}
													value={chartDraft['data-comp-show-points'] || 'false'}
													onValueChange={(v) => (chartDraft['data-comp-show-points'] = v)}
												/>
											</div>
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Fill area</span>
												<Segmented
													options={[
														{ value: 'true', label: 'On' },
														{ value: 'false', label: 'Off' }
													]}
													value={chartDraft['data-comp-area-fill'] || 'false'}
													onValueChange={(v) => (chartDraft['data-comp-area-fill'] = v)}
												/>
											</div>
										{/if}
										{#if draftType === 'bar_chart'}
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Bar width</span>
												<Segmented
													options={[
														{ value: 'narrow', label: 'Narrow' },
														{ value: 'medium', label: 'Medium' },
														{ value: 'wide', label: 'Wide' }
													]}
													value={chartDraft['data-comp-bar-width'] || 'medium'}
													onValueChange={(v) => (chartDraft['data-comp-bar-width'] = v)}
												/>
											</div>
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Corners</span>
												<Segmented
													options={[
														{ value: 'rounded', label: 'Rounded' },
														{ value: 'square', label: 'Square' }
													]}
													value={chartDraft['data-comp-bar-corners'] || 'rounded'}
													onValueChange={(v) => (chartDraft['data-comp-bar-corners'] = v)}
												/>
											</div>
										{/if}
										{#if draftType === 'donut_chart'}
											<div class="flex min-w-0 items-center gap-2">
												<span class="text-muted-foreground w-24 shrink-0 text-xs">Ring width</span>
												<Segmented
													options={[
														{ value: 'thin', label: 'Thin' },
														{ value: 'medium', label: 'Medium' },
														{ value: 'thick', label: 'Thick' }
													]}
													value={chartDraft['data-comp-ring-width'] || 'medium'}
													onValueChange={(v) => (chartDraft['data-comp-ring-width'] = v)}
												/>
											</div>
										{/if}
									</div>

									<!-- TYPOGRAPHY LIVES HERE, NOT IN THE STYLE RAIL. Every word in a chart — the
									     title, the axis ticks, the legend, the data labels — is drawn by the
									     renderer inside the SVG, so a font set on the wrapper div reaches none of
									     them; the rail's Typography sector was hidden for charts because it was a
									     set of controls that changed nothing. Size is ONE number the rest scale
									     from, because a chart with independently-sized ticks and legend is a chart
									     nobody can keep consistent. -->
									<p
										class="text-foreground border-border mt-1 border-t pt-3 text-[11px] font-bold tracking-[0.08em] uppercase"
									>
										Typography
									</p>
									<div class="grid grid-cols-2 gap-x-4 gap-y-2">
										<div class="flex items-center justify-between gap-2">
											<span class="text-muted-foreground text-xs">Font</span>
											<SelectMenu
												size="sm"
												value={chartDraft['data-comp-font-family'] ?? ''}
												items={CHART_FONTS.map((f) => ({ value: f.value, label: f.label }))}
												onValueChange={(v) => (chartDraft['data-comp-font-family'] = v)}
											/>
										</div>
										<div class="flex items-center justify-between gap-2">
											<span class="text-muted-foreground text-xs">Text size</span>
											<input
												type="number"
												min="6"
												max="24"
												step="0.5"
												placeholder="9"
												value={chartDraft['data-comp-font-size'] ?? ''}
												oninput={(e: Event) =>
													(chartDraft['data-comp-font-size'] = (
														e.target as HTMLInputElement
													).value)}
												class="border-input h-7 w-14 rounded-md border bg-transparent px-2 text-center text-xs outline-none"
											/>
										</div>
										<div class="flex items-center justify-between gap-2">
											<span class="text-muted-foreground text-xs">Text colour</span>
											<div class="flex items-center gap-2">
												<ColorInput
													value={chartDraft['data-comp-text-color'] || '#1e293b'}
													swatches={tenantPalette}
													class="max-w-40"
													oninput={(v) => (chartDraft['data-comp-text-color'] = v)}
													onchange={(v) => (chartDraft['data-comp-text-color'] = v)}
												/>
												<!-- A WAY BACK TO THE DEFAULT. `<input type=color>` has no empty state —
												     it always reports a colour — so without this an author who touched
												     the picker could never return the chart to the renderer's own tone. -->
												<Button
													size="sm"
													variant="ghost"
													class="h-7 px-2 text-[10px]"
													disabled={!chartDraft['data-comp-text-color']}
													onclick={() => (chartDraft['data-comp-text-color'] = '')}
												>
													Reset
												</Button>
											</div>
										</div>
									</div>
								</div>
							{/if}
						{/if}
					</div>
				</div>
			</div>

			<!-- Footer -->
			<div class="flex justify-end gap-2 border-t px-5 py-3">
				<Button variant="outline" size="sm" onclick={() => (chartConfigOpen = false)}>Cancel</Button
				>
				<Button variant="brand" size="sm" onclick={applyChartConfig}>Apply</Button>
			</div>
		</div>
	</div>
{/if}

<!-- Colour field: the design system's own picker plus a hex text input, committing on change
     rather than on drag so the undo history stays one step per gesture. Shared by the table Design
     controls.

     `ColorInput`, NOT `<input type="color">`: docs/design-language.md forbids a native control, and
     the browser's own colour dialog cannot offer the firm's palette — which is the answer almost
     every one of these questions actually wants. -->
{#snippet jtSwatch(value: string, onPick: (v: string) => void)}
	<div class="flex items-center gap-2">
		<ColorInput {value} swatches={tenantPalette} onchange={onPick} />
		<input
			{value}
			onchange={(e: Event) => onPick((e.target as HTMLInputElement).value.trim())}
			spellcheck="false"
			aria-label="Colour hex"
			class="border-input focus:border-brand text-foreground h-8 w-full min-w-0 rounded-md border bg-transparent px-2 font-mono text-[11px] uppercase outline-none transition-colors"
		/>
	</div>
{/snippet}

<!-- Styled table mini-preview for the right rail. `inner` already carries the shared
     .doc-jtable-inner rules (built in the script) so the preview matches the canvas/PDF exactly;
     white backdrop = the page. -->
{#snippet tableViz(inner: string)}
	<div class="bg-doc-paper overflow-x-auto rounded-md border p-2">
		<!-- eslint-disable-next-line svelte/no-at-html-tags -- style rules are a constant; data-table cells are escaped, text-table content is the author's own template (same trust as the canvas) -->
		{@html inner}
	</div>
{/snippet}

<!-- Jinja table config — spacious modal (mirrors the chart config dialog) -->
{#if jtableConfigOpen && isJtable}
	<div class="fixed inset-0 z-50 flex items-center justify-center">
		<!-- Ink-derived scrim — see the chart config modal above for why this is not a literal wash. -->
		<button
			type="button"
			class="absolute inset-0 bg-[color-mix(in_oklab,var(--hero-base)_55%,transparent)]"
			aria-label="Close"
			onclick={() => (jtableConfigOpen = false)}
		></button>
		<!-- Wider than a form needs, because the visual mode is a TABLE being edited in place: at 720px
		     a five-column table's own cells were narrower than the labels above them. -->
		<div
			class="bg-background relative z-10 flex max-h-[90svh] w-[min(1080px,94vw)] flex-col overflow-hidden rounded-xl border shadow-2xl"
		>
			<div class="flex items-center justify-between border-b px-5 py-4">
				<Segmented
					options={[
						{ value: 'visual', label: 'Visual' },
						{ value: 'raw', label: 'Raw' }
					]}
					value={jtMode}
					onValueChange={(v) => (jtMode = v as 'visual' | 'raw')}
				/>
				<button
					type="button"
					class="text-muted-foreground hover:text-foreground"
					onclick={() => (jtableConfigOpen = false)}
					aria-label="Close"
				>
					<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"
						><path
							d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06z"
						/></svg
					>
				</button>
			</div>

			<div class="min-h-0 flex-1 overflow-y-auto">
				<div class="space-y-5 p-5">
					{#if jtMode === 'visual'}
						{#if jtCustom}
							<p class="text-muted-foreground text-xs leading-relaxed">
								This table uses a custom template. Edit it in <b>Raw</b> mode — the visual editor can't
								represent it, and switching back would overwrite your changes.
							</p>
						{:else}
							<div class="max-w-xs space-y-1.5">
								<Label for="jt-dataset-picker" class="text-muted-foreground text-[11px] font-medium"
									>Dataset</Label
								>
								<!-- Real dataset picker (was free text over a demo list). -->
								<SelectMenu
									id="jt-dataset-picker"
									items={collectionItems(jtDataset, '— Choose a dataset —')}
									value={jtDataset}
									onValueChange={jtSetDataset}
								/>
							</div>
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<Label class="text-muted-foreground text-[11px] font-medium">Columns</Label>
									<div class="flex items-center gap-1.5">
										{#if jtDatasetCols.length}
											<button
												type="button"
												onclick={jtPrefillFromDataset}
												title="Replace the columns with every field from “{jtDataset}”"
												class="text-brand/80 hover:text-brand hover:bg-brand/10 rounded px-2 py-1 text-[11px] font-medium transition-colors"
												>Prefill from dataset</button
											>
										{/if}
										<button
											type="button"
											onclick={jtAddColumn}
											class="text-brand/80 hover:text-brand hover:bg-brand/10 rounded px-2 py-1 text-[11px] font-medium transition-colors"
											>+ Add column</button
										>
									</div>
								</div>
								<!-- Column header row -->
								<div
									class="text-muted-foreground grid grid-cols-[18px_minmax(0,1fr)_minmax(0,1.5fr)_auto] items-center gap-2.5 px-1 text-[10px] font-semibold uppercase tracking-wide"
								>
									<span></span>
									<span>Header</span>
									<span>Value &amp; filters</span>
									<span></span>
								</div>
								{#each jtColumns as col, i (i)}
									<div
										role="listitem"
										class="rounded-md {jtDragOver === i && jtDragFrom !== i
											? 'ring-brand ring-2 ring-offset-1'
											: ''}"
										ondragover={(e) => jtColDragOver(e, i)}
										ondrop={(e) => jtColDrop(e, i)}
										ondragend={jtColDragEnd}
									>
										<div
											class="grid grid-cols-[18px_minmax(0,1fr)_minmax(0,1.5fr)_auto] items-center gap-2.5"
										>
											<span
												class="text-muted-foreground/40 hover:text-muted-foreground cursor-grab active:cursor-grabbing"
												draggable="true"
												ondragstart={(e) => jtColDragStart(e, i)}
												aria-label="Drag to reorder column {i + 1}"
												role="button"
												tabindex="-1"><GripVerticalIcon class="size-4" /></span
											>
											<input
												value={col.header}
												onchange={(e: Event) =>
													jtSetHeader(i, (e.target as HTMLInputElement).value)}
												placeholder="Header"
												aria-label="Column {i + 1} header"
												class="border-input focus:border-brand text-foreground h-9 w-full min-w-0 rounded-md border bg-transparent px-3 text-sm outline-none transition-colors"
											/>
											<div class="flex min-w-0 items-center gap-1.5">
												<input
													value={splitExpr(col.expr).base}
													onchange={(e: Event) =>
														jtSetBase(i, (e.target as HTMLInputElement).value)}
													placeholder="row.field"
													list="jt-cols"
													aria-label="Column {i + 1} value"
													class="border-input focus:border-brand text-foreground h-9 w-full min-w-0 flex-1 rounded-md border bg-transparent px-3 font-mono text-xs outline-none transition-colors"
												/>
												{#if availableFilters(splitExpr(col.expr).filters).length}
													<SelectMenu
														items={filterOptgroups(splitExpr(col.expr).filters).map((grp) => ({
															label: grp.label,
															options: grp.items.map((f) => ({
																value: f.snippet,
																label: f.label
															}))
														}))}
														bind:value={jtFilterPick}
														onValueChange={(v) => {
															jtAddFilter(i, v);
															jtFilterPick = '';
														}}
														placeholder="ƒ Filter"
														class="w-auto shrink-0"
													/>
												{/if}
											</div>
											<!-- WIDTH AND ALIGNMENT, on the row that already owns this column. A jtable shared
											     its box equally by content, so a reference column took as much room as a
											     description and a column of figures could not be lined up on its digits.
											     Blank width is automatic; a table with none keeps content-sized columns. -->
											<input
												type="number"
												min="1"
												max="100"
												placeholder="auto"
												value={col.width ?? ''}
												oninput={(e: Event) => jtSetWidth(i, (e.target as HTMLInputElement).value)}
												aria-label="Width of column {i + 1} as a percentage"
												title="Column width (%) — blank for automatic"
												class="border-input focus:border-brand text-foreground h-9 w-16 shrink-0 rounded-md border bg-transparent px-2 text-center text-xs outline-none transition-colors"
											/>
											<Segmented
												class="shrink-0"
												options={[
													{ value: 'left', label: 'L' },
													{ value: 'center', label: 'C' },
													{ value: 'right', label: 'R' }
												]}
												value={col.align || 'left'}
												onValueChange={(v) => jtSetAlign(i, v)}
											/>
											<button
												type="button"
												onclick={() => jtRemoveColumn(i)}
												disabled={jtColumns.length <= 1}
												aria-label="Remove column {i + 1}"
												class="text-muted-foreground hover:text-destructive rounded p-1 transition-colors disabled:cursor-not-allowed disabled:opacity-30"
												><XIcon class="size-4" /></button
											>
										</div>
										{#if splitExpr(col.expr).filters.length}
											<div
												class="flex flex-wrap items-center gap-1 pb-1 pl-[calc(16px+0.5rem)] pt-1.5"
											>
												{#each splitExpr(col.expr).filters as f, fi (fi)}
													<span
														class="bg-brand/10 text-brand inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono text-[11px]"
													>
														{f}
														<button
															type="button"
															onclick={() => jtRemoveFilter(i, fi)}
															aria-label="Remove filter"
															class="hover:text-destructive leading-none">×</button
														>
													</span>
												{/each}
											</div>
										{/if}
									</div>
								{/each}
								{#if jtDatasetCols.length}
									<datalist id="jt-cols">
										{#each jtDatasetCols as fld (fld)}
											<option value="row.{fld}"></option>
										{/each}
									</datalist>
								{/if}
							</div>
						{/if}
						<!-- Design — visual styling layered over the template (kept out of the raw source). -->
						<div class="space-y-3 border-t pt-4">
							<div class="flex items-center justify-between">
								<Label class="text-muted-foreground text-[11px] font-medium">Design</Label>
								<button
									type="button"
									onclick={jtResetDesign}
									class="text-muted-foreground hover:text-foreground rounded px-2 py-1 text-[11px] font-medium transition-colors"
									>Reset</button
								>
							</div>
							<div class="grid grid-cols-2 gap-x-4 gap-y-3">
								<div class="space-y-1.5">
									<div class="flex items-center justify-between">
										<Label class="text-muted-foreground text-[11px]">Header background</Label>
										<div class="flex items-center gap-1.5">
											<span class="text-muted-foreground/80 text-[11px]">Transparent</span>
											<Switch
												checked={headerTransparent}
												aria-label="Transparent header background"
												onCheckedChange={toggleHeaderTransparent}
											/>
										</div>
									</div>
									{#if headerTransparent}
										<p class="text-muted-foreground/70 pt-1 text-[11px]">
											No fill — the page shows through behind the header.
										</p>
									{:else}
										{@render jtSwatch(jtDesign.headerBg, (v) => jtSetDesign({ headerBg: v }))}
									{/if}
								</div>
								<div class="space-y-1.5">
									<Label class="text-muted-foreground text-[11px]">Header text</Label>
									{@render jtSwatch(jtDesign.headerFg, (v) => jtSetDesign({ headerFg: v }))}
								</div>
								<div class="space-y-1.5">
									<div class="flex items-center justify-between">
										<Label class="text-muted-foreground text-[11px]">Row background</Label>
										<div class="flex items-center gap-1.5">
											<span class="text-muted-foreground/80 text-[11px]">Transparent</span>
											<Switch
												checked={rowTransparent}
												aria-label="Transparent row background"
												onCheckedChange={toggleRowTransparent}
											/>
										</div>
									</div>
									{#if rowTransparent}
										<p class="text-muted-foreground/70 pt-1 text-[11px]">
											No fill — the page shows through the rows.
										</p>
									{:else}
										{@render jtSwatch(jtDesign.rowBg, (v) => jtSetDesign({ rowBg: v }))}
									{/if}
								</div>
								<div class="space-y-1.5">
									<Label class="text-muted-foreground text-[11px]">Body text</Label>
									{@render jtSwatch(jtDesign.bodyFg, (v) => jtSetDesign({ bodyFg: v }))}
								</div>
								<div class="space-y-1.5">
									<Label class="text-muted-foreground text-[11px]">Borders</Label>
									<Segmented
										class="w-full"
										options={JT_BORDER_OPTS.map((o) => ({ value: o.value, label: o.label }))}
										value={jtDesign.borders}
										onValueChange={(v) => jtSetDesign({ borders: v as JDesign['borders'] })}
									/>
								</div>
								<div class="space-y-1.5">
									<Label class="text-muted-foreground text-[11px]">Border colour</Label>
									{@render jtSwatch(jtDesign.borderColor, (v) => jtSetDesign({ borderColor: v }))}
								</div>
								<div class="space-y-1.5">
									<div class="flex items-center justify-between">
										<Label class="text-muted-foreground text-[11px]">Striped rows</Label>
										<Switch
											checked={jtDesign.striped}
											aria-label="Striped rows"
											onCheckedChange={(v) => jtSetDesign({ striped: v })}
										/>
									</div>
									{#if jtDesign.striped}
										{@render jtSwatch(jtDesign.stripeColor, (v) => jtSetDesign({ stripeColor: v }))}
									{:else}
										<p class="text-muted-foreground/70 pt-1 text-[11px]">
											Alternate-row shading is off.
										</p>
									{/if}
								</div>
								<div class="space-y-1.5">
									<Label class="text-muted-foreground text-[11px]">Text alignment</Label>
									<Segmented
										class="w-full"
										options={JT_ALIGN_OPTS.map((o) => ({ value: o.value, label: o.label }))}
										value={jtDesign.align}
										onValueChange={(v) => jtSetDesign({ align: v as JDesign['align'] })}
									/>
								</div>
								<div class="space-y-1.5">
									<Label class="text-muted-foreground text-[11px]">Font size (px)</Label>
									<input
										type="number"
										min="8"
										max="24"
										value={jtDesign.fontSize}
										onchange={(e: Event) =>
											jtSetDesign({ fontSize: Number((e.target as HTMLInputElement).value) || 13 })}
										class="border-input focus:border-brand text-foreground h-8 w-full rounded-md border bg-transparent px-2 text-xs outline-none transition-colors"
									/>
								</div>
							</div>
						</div>
					{:else}
						<div class="space-y-2">
							<Label class="text-muted-foreground text-[11px] font-medium"
								>Jinja / HTML source</Label
							>
							<Textarea
								bind:value={jtRaw}
								onblur={applyJtableRaw}
								spellcheck="false"
								rows={14}
								placeholder={'<table>…{% for row in dataset %}…{% endfor %}…</table>'}
								class="h-72 resize-y p-3 font-mono text-xs leading-relaxed"
							/>
							<p class="text-muted-foreground text-[11px] leading-relaxed">
								Changes apply when you click away. The repeating row goes inside
								<span class="font-mono">{'{% for row in … %}'}</span> …
								<span class="font-mono">{'{% endfor %}'}</span>. Cells use
								<span class="font-mono">{'{{ row.field }}'}</span>.
							</p>
							{#if jtRawLint.length}
								<div
									class="border-warning/30 bg-warning/10 text-warning space-y-0.5 rounded-md border p-2.5 text-[11px]"
								>
									{#each jtRawLint as issue (issue)}
										<p>⚠ {issue}</p>
									{/each}
								</div>
							{/if}
						</div>
					{/if}
				</div>
			</div>

			<div class="flex justify-end gap-2 border-t px-5 py-3">
				<Button variant="brand" size="sm" onclick={() => (jtableConfigOpen = false)}>Done</Button>
			</div>
		</div>
	</div>
{/if}

<!-- Hidden file input for template import -->
<input
	bind:this={importFileEl}
	type="file"
	accept=".json,application/json"
	class="hidden"
	onchange={handleImportFile}
	aria-hidden="true"
/>
