import type { StatusTone } from '../components/ui/status-badge/index.js';

// ─────────────────────────────────────────────────────────────────────────
// Template domain types — shared by the platform and admin portals.
// App-specific data (template lists, datasets, content store) lives in each
// app's `$lib/demo`; this module owns the shapes + the editor engine.
//
// Note on colours: the `*_META` maps below are app CHROME (badges in the list/
// detail UI) and so use design tokens. The document *preview* canvas
// (template-page.svelte) intentionally keeps print styling (white paper, ink
// text, brand teal) so it looks like the rendered PDF, not the app theme.
// ─────────────────────────────────────────────────────────────────────────

export type TemplateStatus = 'draft' | 'published' | 'archived';
// Document type is an OPEN-ENDED tag (the tenant's documents.document_types registry), not a fixed
// union. DOC_TYPE_META gives chrome (label + colours) for the common/known slugs; docTypeMeta()
// falls back to a prettified label + neutral colours for any custom tag, so an unknown type never
// crashes a consumer that indexes the map.
export type DocumentType = string;
export type Renderer = 'weasy' | 'chromium';
/** A dataset's two independent axes: FORMAT (how the data is defined) × LIVENESS (static vs live). */
export type DatasetFormat = 'catalogue' | 'sql' | 'csv' | 'inline';
export type DatasetScope = 'subject' | 'collection' | 'global';

export const TEMPLATE_STATUS_META: Record<TemplateStatus, { label: string; tone: StatusTone }> = {
	draft: { label: 'Draft', tone: 'neutral' },
	published: { label: 'Published', tone: 'success' },
	archived: { label: 'Archived', tone: 'neutral' }
};

// Chrome for the well-known slugs — the registry's seeded defaults plus the older template
// taxonomy, kept for existing templates. Any slug not here resolves through docTypeMeta()'s
// neutral fallback, so the map is safe to extend without touching consumers.
export const DOC_TYPE_META: Record<string, { label: string; textColor: string; bgColor: string }> =
	{
		report: { label: 'Report', textColor: 'text-primary', bgColor: 'bg-primary/10' },
		suitability: { label: 'Suitability', textColor: 'text-primary', bgColor: 'bg-primary/10' },
		illustration: { label: 'Illustration', textColor: 'text-brand', bgColor: 'bg-brand/10' },
		valuation: { label: 'Valuation', textColor: 'text-info', bgColor: 'bg-info/10' },
		review: { label: 'Review', textColor: 'text-warning', bgColor: 'bg-warning/10' },
		factfind: { label: 'Fact-find', textColor: 'text-success', bgColor: 'bg-success/10' },
		statement: { label: 'Statement', textColor: 'text-muted-foreground', bgColor: 'bg-muted' },
		agreement: { label: 'Agreement', textColor: 'text-success', bgColor: 'bg-success/10' },
		identity: { label: 'Identity', textColor: 'text-info', bgColor: 'bg-info/10' },
		tax: { label: 'Tax', textColor: 'text-warning', bgColor: 'bg-warning/10' },
		application: { label: 'Application', textColor: 'text-primary', bgColor: 'bg-primary/10' },
		letter: { label: 'Letter', textColor: 'text-muted-foreground', bgColor: 'bg-muted' }
	};

/** Chrome for a document-type slug: the curated entry when known, else a prettified label + neutral
 *  colours — so an open-ended/custom tag renders cleanly instead of breaking a bare map lookup. */
export function docTypeMeta(slug: string): { label: string; textColor: string; bgColor: string } {
	return (
		DOC_TYPE_META[slug] ?? {
			label: slug
				? slug.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
				: 'Unclassified',
			textColor: 'text-muted-foreground',
			bgColor: 'bg-muted'
		}
	);
}

export const DATASET_FORMAT_META: Record<
	DatasetFormat,
	{ label: string; textColor: string; bgColor: string }
> = {
	// A dataset Graphene provides — the firm picks it and sets its date range; the query is ours.
	catalogue: { label: 'Provided', textColor: 'text-info', bgColor: 'bg-info/10' },
	// `sql` and `csv` are LEGACY: free-text SQL authoring is gone (a firm could type any SELECT,
	// guarded only by a keyword denylist) and the server-side CSV format was never reachable. They
	// stay in the vocabulary so a row written before that still renders with a label rather than a
	// blank badge.
	sql: { label: 'SQL', textColor: 'text-muted-foreground', bgColor: 'bg-muted' },
	csv: { label: 'CSV', textColor: 'text-muted-foreground', bgColor: 'bg-muted' },
	inline: { label: 'Table', textColor: 'text-primary', bgColor: 'bg-primary/10' }
};

export const DATASET_SCOPE_META: Record<DatasetScope, { label: string }> = {
	subject: { label: 'Subject' },
	collection: { label: 'Collection' },
	global: { label: 'Global' }
};

export interface TemplateVersion {
	uid: string;
	versionNumber: number;
	status: TemplateStatus;
	renderer: Renderer;
	createdAt: string;
	createdBy: string;
	publishedAt?: string;
	publishedBy?: string;
}

export interface Template {
	uid: string;
	code: string;
	name: string;
	documentType: DocumentType;
	description?: string;
	currentVersionNumber?: number;
	currentVersionStatus?: TemplateStatus;
	archivedAt?: string;
	createdAt: string;
	createdBy: string;
	versionCount: number;
	linkedDatasets?: string[];
	/** The form advisers fill at generate time (a Form `code`). See docs/forms.md §9.1. */
	inputFormCode?: string;
}

export interface Dataset {
	uid: string;
	code: string;
	name: string;
	format: DatasetFormat;
	scope: DatasetScope;
	enabled: boolean;
	versionCount: number;
	publishedVersionNumber?: number;
	description?: string;
	createdAt: string;
	createdBy: string;
	/** Shipped by Graphene: no row of the firm's own, so it cannot be edited or deleted. */
	provided?: boolean;
	/** The catalogue entry this draws on. */
	catalogueKey?: string;
}

// ─────────────────────────────────────────────────────────────────────────
// Page / block model (the editor + preview content).
// ─────────────────────────────────────────────────────────────────────────

export type PageSize = 'A4' | 'A5' | 'A3' | 'Letter' | 'Legal';
export type Orientation = 'portrait' | 'landscape';
export type BlockAlign = 'left' | 'center' | 'right' | 'justify';

export type BlockType =
	| 'heading'
	| 'paragraph'
	| 'image'
	| 'labelValue'
	| 'bulletList'
	| 'numberedList'
	| 'divider'
	| 'noteBox'
	| 'highlightBox'
	| 'spacer'
	| 'columns'
	| 'chart'
	| 'table'
	| 'pageBreak'
	| 'conditional'
	| 'repeat';

export type PageBackground = 'none' | 'color';
export type BorderStyle = 'none' | 'solid' | 'dashed' | 'dotted';

export interface TemplateBlock {
	uid: string;
	type: BlockType;
	/** heading/paragraph/box text, or newline-separated list items. May contain {{ merge }} tokens. */
	text?: string;
	/** heading level (1 = largest). */
	level?: 1 | 2 | 3;
	align?: BlockAlign;
	/** spacer height in px. */
	height?: number;
	/** image wordmark, label+value label, or box/chart/table title. */
	label?: string;
	/** chart kind (bar|line|pie), table kind (data|holdings|projections), columns (full|two). */
	variant?: string;
	// Typography overrides (Component panel). Unset = inherit theme defaults.
	fontSize?: number;
	lineHeight?: number;
	bold?: boolean;
	italic?: boolean;
	underline?: boolean;
	color?: string;
}

export interface TemplatePage {
	uid: string;
	blocks: TemplateBlock[];
}

export interface Margins {
	top: number;
	right: number;
	bottom: number;
	left: number;
}

export interface TemplateContent {
	size: PageSize;
	orientation: Orientation;
	margins: Margins;
	background: PageBackground;
	border: BorderStyle;
	pages: TemplatePage[];
}

/** Page dimensions in millimetres (portrait). */
export const PAGE_DIMS: Record<PageSize, { w: number; h: number }> = {
	A4: { w: 210, h: 297 },
	A5: { w: 148, h: 210 },
	A3: { w: 297, h: 420 },
	Letter: { w: 216, h: 279 },
	Legal: { w: 216, h: 356 }
};

export const PAGE_SIZES: PageSize[] = ['A4', 'A5', 'A3', 'Letter', 'Legal'];

/** 96 dpi: 1mm ≈ 3.7795px. */
export const MM_TO_PX = 3.7795;

export const BLOCK_LABELS: Record<BlockType, string> = {
	heading: 'Heading',
	paragraph: 'Text',
	image: 'Image',
	labelValue: 'Label + Value',
	bulletList: 'Bullet List',
	numberedList: 'Numbered List',
	divider: 'Divider',
	noteBox: 'Note Box',
	highlightBox: 'Highlight Box',
	spacer: 'Spacer',
	columns: 'Columns',
	chart: 'Chart',
	table: 'Table',
	pageBreak: 'Page Break',
	conditional: 'Conditional',
	repeat: 'Repeat'
};

// Blocks panel catalogue, grouped like the editor's Blocks tab.
export interface PaletteItem {
	type: BlockType;
	label: string;
	variant?: string;
}
export interface PaletteGroup {
	title: string;
	items: PaletteItem[];
}

export const BLOCK_PALETTE: PaletteGroup[] = [
	{
		title: 'Basic',
		items: [
			{ type: 'paragraph', label: 'Text' },
			{ type: 'image', label: 'Image' },
			{ type: 'labelValue', label: 'Label + Value' },
			{ type: 'bulletList', label: 'Bullet List' },
			{ type: 'numberedList', label: 'Numbered List' },
			{ type: 'divider', label: 'Divider' },
			{ type: 'noteBox', label: 'Note Box' },
			{ type: 'highlightBox', label: 'Highlight Box' }
		]
	},
	{
		title: 'Columns',
		items: [
			{ type: 'columns', label: 'Full Width', variant: 'full' },
			{ type: 'columns', label: 'Add Layout', variant: 'two' }
		]
	},
	{
		// One configurable Chart (kind — bar/line/pie — is switched in the Component panel).
		title: 'Charts',
		items: [{ type: 'chart', label: 'Chart', variant: 'bar' }]
	},
	{
		// One Table (the example/preset tables were removed to match the platform editor).
		title: 'Tables',
		items: [{ type: 'table', label: 'Table', variant: 'data' }]
	},
	{
		title: 'Logic',
		items: [
			{ type: 'repeat', label: 'Repeat (Loop)' },
			{ type: 'conditional', label: 'Conditional' }
		]
	}
];

export interface NewTemplateInput {
	name: string;
	code: string;
	documentType: DocumentType;
	renderer: Renderer;
	description?: string;
	linkedDatasets?: string[];
	inputFormCode?: string;
}
