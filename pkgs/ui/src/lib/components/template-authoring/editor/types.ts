/**
 * Template-editor types — the variable schema authored alongside the GrapesJS
 * document, and the component-def shape emitted on save. Ported from the
 * reference editor (platform-1 `template-editor.tsx`) and kept framework-agnostic.
 */

export interface VarField {
	key: string;
	label: string;
	type?: 'string' | 'number' | 'date' | 'currency' | 'boolean';
	sample?: string | number;
}

export interface LoopDef {
	key: string;
	label: string;
	itemVar?: string;
}

export interface ColourDef {
	name: string;
	label: string;
	hex: string;
}

export interface HFColumn {
	content: string;
	align: 'left' | 'center' | 'right';
	fr: number;
}

export interface FooterDef {
	enabled: boolean;
	columns: HFColumn[];
	color: string;
	borderColor: string;
	background: string;
	fontSize: string;
	paddingH: string;
	paddingV: string;
}

export interface PageSize {
	width: string;
	height: string;
}

export interface VarSchema {
	variables: VarField[];
	loops: LoopDef[];
	colours: ColourDef[];
	layout: { stickyFooter: boolean };
	/** Per-page header configs — indexed by page number; undefined entry = no header on that page. */
	headers: FooterDef[];
	/** Per-page footer configs — indexed by page number; undefined entry = no footer on that page. */
	footers: FooterDef[];
	pageSize: PageSize;
}

/** A typed component (chart/table) lifted from the canvas on save — the contract
    the Python render pipeline consumes. */
export interface ComponentDef {
	id: string;
	type: string;
	props: Record<string, unknown>;
	binding: Record<string, unknown>;
}

/** The persisted `template_versions.schema` payload (see the generation spec §5.2). */
export interface TemplateSchema {
	html: string;
	css: string;
	components: ComponentDef[];
	datasets: string[];
	calculations: string[];
	gjs_project: unknown;
	variable_schema: VarSchema;
	pageSize: PageSize;
}

export function defaultFooter(): FooterDef {
	return {
		enabled: true,
		columns: [
			{ content: '{{ date }}', align: 'left', fr: 1 },
			{ content: '', align: 'center', fr: 1 },
			{ content: '', align: 'right', fr: 1 }
		],
		color: '',
		borderColor: '',
		background: '',
		fontSize: '',
		paddingH: '72px',
		paddingV: '8px'
	};
}

export function defaultHeader(): FooterDef {
	return {
		enabled: true,
		columns: [
			{ content: '', align: 'left', fr: 1 },
			{ content: '', align: 'center', fr: 1 },
			{ content: '', align: 'right', fr: 1 }
		],
		color: '',
		borderColor: '',
		background: '',
		fontSize: '',
		paddingH: '72px',
		paddingV: '8px'
	};
}

export function defaultSchema(): VarSchema {
	return {
		variables: [],
		loops: [],
		colours: [],
		layout: { stickyFooter: false },
		headers: [],
		footers: [],
		pageSize: { width: '210mm', height: '297mm' }
	};
}

function parseFooterDef(raw: unknown, defaults: FooterDef): FooterDef {
	if (!raw || typeof raw !== 'object') return defaults;
	const r = raw as Record<string, unknown>;

	let columns: HFColumn[];
	if (Array.isArray(r.columns) && r.columns.length > 0) {
		columns = (r.columns as Record<string, unknown>[]).map((c) => ({
			content: String(c.content ?? ''),
			align: (['left', 'center', 'right'] as const).includes(c.align as 'left' | 'center' | 'right')
				? (c.align as 'left' | 'center' | 'right')
				: 'left',
			fr: typeof c.fr === 'number' && c.fr > 0 ? c.fr : 1
		}));
	} else {
		// Migrate legacy left/center/right fields
		const defCols = defaults.columns;
		columns = [
			{ content: String(r.left ?? defCols[0]?.content ?? ''), align: 'left', fr: 1 },
			{ content: String(r.center ?? ''), align: 'center', fr: 1 },
			{ content: String(r.right ?? ''), align: 'right', fr: 1 }
		];
	}

	return {
		enabled: Boolean(r.enabled),
		columns,
		color: String(r.color ?? ''),
		borderColor: String(r.borderColor ?? ''),
		background: String(r.background ?? ''),
		fontSize: String(r.fontSize ?? ''),
		paddingH: String(r.paddingH ?? '72px'),
		paddingV: String(r.paddingV ?? '8px')
	};
}

/** Coerce arbitrary stored JSON into a VarSchema (defensive — mirrors the reference).
    Handles both the legacy single-object format and the current per-page array format. */
export function parseSchema(v: unknown): VarSchema {
	if (!v || typeof v !== 'object') return defaultSchema();
	const s = v as Record<string, unknown>;
	const pageSize = s.pageSize as Record<string, unknown> | undefined;

	// Migrate legacy single-object header/footer into per-page arrays.
	let headers: FooterDef[] = [];
	let footers: FooterDef[] = [];
	if (Array.isArray(s.headers)) {
		headers = (s.headers as unknown[]).map((h) => parseFooterDef(h, defaultHeader()));
	} else if (s.header && typeof s.header === 'object') {
		const h = parseFooterDef(s.header, defaultHeader());
		if (h.enabled) headers = [h];
	}
	if (Array.isArray(s.footers)) {
		footers = (s.footers as unknown[]).map((f) => parseFooterDef(f, defaultFooter()));
	} else if (s.footer && typeof s.footer === 'object') {
		const f = parseFooterDef(s.footer, defaultFooter());
		if (f.enabled) footers = [f];
	}

	return {
		variables: Array.isArray(s.variables) ? (s.variables as VarField[]) : [],
		loops: Array.isArray(s.loops) ? (s.loops as LoopDef[]) : [],
		colours: Array.isArray(s.colours) ? (s.colours as ColourDef[]) : [],
		layout:
			typeof s.layout === 'object' && s.layout !== null
				? { stickyFooter: Boolean((s.layout as Record<string, unknown>).stickyFooter) }
				: { stickyFooter: false },
		headers,
		footers,
		pageSize: pageSize
			? { width: String(pageSize.width || '210mm'), height: String(pageSize.height || '297mm') }
			: { width: '210mm', height: '297mm' }
	};
}
