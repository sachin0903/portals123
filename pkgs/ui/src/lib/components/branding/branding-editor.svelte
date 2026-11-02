<script lang="ts">
	import { untrack } from 'svelte';
	import {
		googleFontHref,
		colorToHex,
		heroInk,
		generateTheme,
		applyTheme,
		applyAccents,
		staffPortalBranding,
		THEME_STYLES,
		THEME_STYLE_KEYS,
		THEME_PRESETS,
		FONT_OPTIONS,
		MONO_FONT_OPTIONS,
		DISPLAY_FONT_OPTIONS,
		type ThemeStyleKey,
		type ThemePreset,
		type Branding,
		type BrandingBlock,
		type BrandingGlass,
		type CardDesign,
		type CardFinish,
		type BrandingPage,
		type ClientPortalTemplate
	} from '@graphene/auth';
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import { Input } from '../ui/input/index.js';
	import EmailInput from '../../forms/field-inputs/email-input.svelte';
	import PhoneNumberInput from '../../forms/field-inputs/phone-number-input.svelte';
	import { Label } from '../ui/label/index.js';
	import { Textarea } from '../ui/textarea/index.js';
	import { Switch } from '../ui/switch/index.js';
	import Segmented from '../ui/segmented/segmented.svelte';
	import SelectMenu from '../ui/select-menu/select-menu.svelte';
	import * as Card from '../ui/card/index.js';
	import * as Select from '../ui/select/index.js';
	import ShellPreview from './shell-preview.svelte';
	import ClassificationCatalogue from './classification-catalogue.svelte';
	import GroupCatalogue from './group-catalogue.svelte';
	import FontPicker from './font-picker.svelte';
	import LayoutBuilder from './layout-builder.svelte';
	import PagesRail from './pages-rail.svelte';
	import BlockInspector from './block-inspector.svelte';
	import {
		DEFAULT_CARD_DESIGNS,
		FINISHES,
		FINISH_LABELS,
		accountFace,
		designFor
	} from '../../categorical.js';
	import type { CardProductType } from './card-products.js';
	import { materialiseWorkingPages } from './pages-view.js';
	import {
		customise,
		isCustomised,
		layoutFor,
		revert,
		setLayout,
		type Audience,
		type AudienceTab
	} from './page-audience.js';
	import { duplicateBlock } from './page-ops.js';
	import { PAGE_PRESETS, presetByKey, type PagePreset } from './page-presets.js';
	import { PREVIEW_DEVICES, type PreviewDevice } from './preview-devices.js';
	import {
		assignTemplateClassifications,
		assignTemplateGroups,
		duplicateTemplate,
		MAX_PORTAL_TEMPLATES,
		previewTemplateBranding,
		templateFromBranding,
		withWorkingPages
	} from './portal-templates.js';
	import type { ClassificationDraft, ClientClassification } from './classifications.js';
	import type { ClientGroup, GroupDraft } from './groups.js';
	import type { PortalGridMetrics } from '../investment/masonry.js';
	import { PERSONAS, personaByKey, type Persona } from '../../fixtures/personas.js';
	import { cn } from '../../utils.js';

	// The branding studio. Client portal: pick a preset or a brand/accent + style and the FULL
	// harmonious palette is generated (there is no per-token override: every other colour is an
	// alias of the generated fourteen), and each product type picks a card finish. Adviser portal:
	// identity (logos, icon) and two accents — paper, type and chrome stay the product's. The live
	// preview is the REAL app shell. Owns a working copy of BOTH portals' branding; Save emits
	// { name, client, platform }; uploads via `onupload(portal, kind, file)`.
	type UploadKind = 'logo' | 'logo-dark' | 'icon' | 'favicon' | 'card-art';
	type Portal = 'client' | 'advisor';
	type Scheme = 'light' | 'dark';

	let {
		client,
		platform,
		name,
		classifications,
		groups,
		productTypes = [],
		productTypesUnavailable = false,
		canManage = true,
		canEditPages = true,
		saving = false,
		uploading = false,
		error = null,
		saved = false,
		onsave,
		onupload,
		onSaveClassification,
		onDeleteClassification,
		onSaveGroup,
		onDeleteGroup
	}: {
		client: Branding;
		platform: Branding;
		name: string;
		classifications?: ClientClassification[];
		/** Firm-owned client groups. Undefined hides the catalogue (the admin studio). */
		groups?: ClientGroup[];
		/** The firm's enabled product types (`cardProductTypes` over its shelf). One card-design
		 *  row each. Empty is a firm with nothing enabled, not a prompt to invent wrappers. */
		productTypes?: readonly CardProductType[];
		/** The shelf could not be read, so an empty list must not be presented as "no products". */
		productTypesUnavailable?: boolean;
		canManage?: boolean;
		/** `pages:write` — composing the client portal's pages (the Pages view). Independent of
		 *  `canManage` (`branding:write`, the Design view): without it the Pages view stays
		 *  visible but read-only, so a branding editor can still see the composition without
		 *  being offered edits the API's diff gate would refuse. */
		canEditPages?: boolean;
		saving?: boolean;
		uploading?: boolean;
		error?: string | null;
		saved?: boolean;
		onsave?: (next: { name: string; client: Branding; platform: Branding }) => void;
		onupload?: (portal: Portal, kind: UploadKind, file: File) => Promise<string>;
		onSaveClassification?: (draft: ClassificationDraft) => Promise<void> | void;
		onDeleteClassification?: (uid: string) => Promise<void> | void;
		onSaveGroup?: (draft: GroupDraft) => Promise<void> | void;
		onDeleteGroup?: (uid: string) => Promise<void> | void;
	} = $props();

	// The Pages view's write gate: pages edits need BOTH the pen (canManage — there is no Save
	// without it) and the composition permission. The Design tabs key off canManage alone.
	const pagesEditable = $derived(canManage && canEditPages);

	let draftName = $state(untrack(() => name));
	let drafts = $state<Record<Portal, Branding>>(
		untrack(() => ({
			client: structuredClone($state.snapshot(client)) as Branding,
			advisor: structuredClone($state.snapshot(platform)) as Branding
		}))
	);
	let portal = $state<Portal>('client');
	let selectedTemplateId = $state<string | null>(
		untrack(() => drafts.client.default_template_id ?? drafts.client.templates?.[0]?.id ?? null)
	);
	const clientTemplates = $derived(drafts.client.templates ?? []);
	const selectedTemplate = $derived<ClientPortalTemplate | null>(
		clientTemplates.find((template) => template.id === selectedTemplateId) ?? null
	);
	// `active` is the visual design being edited. Firm identity stays on the root Branding and is
	// merged into the selected template only for preview/runtime.
	const active = $derived<Branding>(
		portal === 'client' && selectedTemplate ? selectedTemplate : drafts[portal]
	);
	const sharedBranding = $derived(drafts[portal]);
	const previewBranding = $derived(
		portal === 'client' ? previewTemplateBranding(drafts.client, selectedTemplate) : drafts.advisor
	);

	// Seed colours + style per portal (the curated inputs). Seeds initialise from the saved palette
	// (converted to hex) so re-opening reflects the current look; else the Graphene defaults.
	// These two hexes are DATA, not styling: they are the initial *value* of a colour picker, and
	// `<input type="color">` cannot take a CSS var — so they stay literal on purpose. They are the
	// hex renderings of the stock --primary / --brand.
	const SEED_DEFAULTS = { primary: '#2b3a55', accent: '#17a2a2' } as const;
	function seedFrom(b: Branding, key: 'primary' | 'brand', fallback: string): string {
		return colorToHex(b.colors?.light?.[key]) ?? fallback;
	}
	let seeds = $state<Record<Portal, { primary: string; accent: string }>>(
		untrack(() => ({
			client: {
				primary: seedFrom(selectedTemplate ?? drafts.client, 'primary', SEED_DEFAULTS.primary),
				accent: seedFrom(selectedTemplate ?? drafts.client, 'brand', SEED_DEFAULTS.accent)
			},
			advisor: {
				primary: seedFrom(drafts.advisor, 'primary', SEED_DEFAULTS.primary),
				accent: seedFrom(drafts.advisor, 'brand', SEED_DEFAULTS.accent)
			}
		}))
	);
	let styleKey = $state<Record<Portal, ThemeStyleKey>>(
		untrack(() => ({ client: 'classic', advisor: 'classic' }))
	);

	let scheme = $state<Scheme>(
		untrack(() => ((selectedTemplate ?? drafts.client).mode === 'dark' ? 'dark' : 'light'))
	);

	// One device shared by BOTH views' previews (Design and Pages), so switching view keeps the
	// frame you were looking at. Deliberately not persisted — a preview zoom is session ephemera.
	let device = $state<PreviewDevice>('desktop');
	const DEVICE_OPTIONS = Object.entries(PREVIEW_DEVICES).map(([value, spec]) => ({
		value,
		label: spec.label
	}));

	type View = 'design' | 'layout';
	let view = $state<View>('design');
	$effect(() => {
		if (portal !== 'client' && view === 'layout') view = 'design';
	});

	// ── Pages view state ────────────────────────────────────────────────────────
	// The working page set materialises ONCE, on first entering the view — and into LOCAL state,
	// not the draft. `drafts.client.pages` is only written by commitPages (an actual edit), so
	// saving an untouched studio keeps `pages` null and a tenant that never customised keeps
	// tracking the platform defaults instead of having today's shape stamped into its record.
	let workingPages = $state<BrandingPage[] | null>(null);
	let selectedPageId = $state<string | null>(null);
	let audienceTab = $state<AudienceTab>('default');
	let personaKey = $state<Persona['key']>('individual2');
	let selectedBlockId = $state<string | null>(null);
	let revertOpen = $state(false);
	let deleteTemplateOpen = $state(false);
	let workingTemplateId = $state<string | null | undefined>(undefined);
	// Set by commitPages. Save only stamps working pages onto the PUT when the firm actually
	// edited — opening the Pages view materialises a local copy and must not freeze today's
	// defaults into the record.
	let pagesDirty = false;

	// What the live preview measured, fed into the builder so its tiles are a miniature of the real grid. Taken from the preview's report, never re-derived from the frame's DOM: PreviewFrame stays a generic viewport simulator.
	let gridMetrics = $state<PortalGridMetrics | null>(null);
	function reportMetrics(next: PortalGridMetrics) {
		// A single-column (below md) frame measures full-width heights, so keep the last spanned reading rather than dropping the builder back to authored spans.
		if (next.spanned) gridMetrics = next;
	}

	$effect(() => {
		if (view === 'layout' && (workingPages === null || workingTemplateId !== selectedTemplateId)) {
			workingPages = materialiseWorkingPages(($state.snapshot(active) as Branding).pages ?? null);
			workingTemplateId = selectedTemplateId;
			selectedPageId = null;
			selectedBlockId = null;
		}
	});
	// Keep the selection valid across page deletions/replacements (rail edits arrive wholesale).
	$effect(() => {
		if (!workingPages || workingPages.length === 0) return;
		if (!workingPages.some((p) => p.id === selectedPageId)) {
			selectedPageId = workingPages[0]?.id ?? null;
			selectedBlockId = null;
		}
	});

	const pagesWorking = $derived(workingPages ?? []);
	const selectedPage = $derived(pagesWorking.find((p) => p.id === selectedPageId) ?? null);
	const persona = $derived(personaByKey(personaKey));
	const editAudience = $derived<Audience | null>(audienceTab === 'default' ? null : audienceTab);
	const audienceCustomised = $derived(
		selectedPage && editAudience ? isCustomised(selectedPage, editAudience) : true
	);
	const currentLayout = $derived(selectedPage ? layoutFor(selectedPage, audienceTab) : []);
	const selectedBlock = $derived(currentLayout.find((b) => b.id === selectedBlockId) ?? null);
	// The frame always shows one concrete audience: the tab being edited, or — on Default — the
	// persona's own (a real client is never "default"; they are always one audience or the other).
	const previewAudience = $derived<Audience>(editAudience ?? persona.audience);

	const AUDIENCE_LABEL: Record<Audience, string> = {
		individual: 'Individuals',
		organisation: 'Trusts & companies'
	};

	function selectTemplate(id: string | null) {
		selectedTemplateId = id;
		heroDrafting = false;
		const next = drafts.client.templates?.find((template) => template.id === id) ?? drafts.client;
		seeds.client = {
			primary: seedFrom(next, 'primary', SEED_DEFAULTS.primary),
			accent: seedFrom(next, 'brand', SEED_DEFAULTS.accent)
		};
		if (next.mode === 'light' || next.mode === 'dark') scheme = next.mode;
		workingPages = null;
		workingTemplateId = undefined;
	}
	function replaceTemplate(next: ClientPortalTemplate) {
		drafts.client.templates = clientTemplates.map((template) =>
			template.id === next.id ? next : template
		);
	}
	function createTemplate() {
		if (clientTemplates.length >= MAX_PORTAL_TEMPLATES) return;
		const next = templateFromBranding(
			$state.snapshot(active) as Branding,
			`Design ${clientTemplates.length + 1}`,
			clientTemplates.map((template) => template.id)
		);
		drafts.client.templates = [...clientTemplates, next];
		drafts.client.default_template_id ??= next.id;
		selectTemplate(next.id);
	}
	function copyTemplate() {
		if (!selectedTemplate) return;
		const next = duplicateTemplate(
			$state.snapshot(selectedTemplate) as ClientPortalTemplate,
			clientTemplates.map((template) => template.id)
		);
		drafts.client.templates = [...clientTemplates, next];
		selectTemplate(next.id);
	}
	function assignBookDesign(classificationSlug: string, designId: string | null) {
		let templates = $state.snapshot(clientTemplates) as ClientPortalTemplate[];
		templates = templates.map((template) => ({
			...template,
			classification_slugs: (template.classification_slugs ?? []).filter(
				(slug) => slug !== classificationSlug
			)
		}));
		if (designId) {
			const current = templates.find((template) => template.id === designId);
			templates = assignTemplateClassifications(templates, designId, [
				...(current?.classification_slugs ?? []),
				classificationSlug
			]);
		}
		drafts.client.templates = templates;
	}
	function assignGroupDesign(groupSlug: string, designId: string | null) {
		let templates = $state.snapshot(clientTemplates) as ClientPortalTemplate[];
		templates = templates.map((template) => ({
			...template,
			group_slugs: (template.group_slugs ?? []).filter((slug) => slug !== groupSlug)
		}));
		if (designId) {
			const current = templates.find((template) => template.id === designId);
			templates = assignTemplateGroups(templates, designId, [
				...(current?.group_slugs ?? []),
				groupSlug
			]);
		}
		drafts.client.templates = templates;
	}
	function deleteTemplate() {
		if (!selectedTemplate) return;
		const remaining = clientTemplates.filter((template) => template.id !== selectedTemplate.id);
		drafts.client.templates = remaining;
		if (drafts.client.default_template_id === selectedTemplate.id) {
			drafts.client.default_template_id = remaining[0]?.id ?? null;
		}
		selectTemplate(remaining[0]?.id ?? null);
	}

	function commitPages(next: BrandingPage[]) {
		workingPages = next;
		pagesDirty = true;
		// Key off the selected id, not the $derived template object: spreading a derived
		// snapshot and then failing to match `template.id === next.id` used to leave drafts
		// untouched while the Pages canvas still showed the edit.
		const templateId = selectedTemplateId;
		const templates = drafts.client.templates;
		if (templateId && templates?.some((template) => template.id === templateId)) {
			drafts.client.templates = templates.map((template) =>
				template.id === templateId ? { ...template, pages: next } : template
			);
			if (!drafts.client.default_template_id || drafts.client.default_template_id === templateId) {
				drafts.client.pages = next;
			}
		} else {
			drafts.client.pages = next;
		}
	}
	function commitPage(next: BrandingPage) {
		commitPages(pagesWorking.map((p) => (p.id === next.id ? next : p)));
	}
	// The page-ops/page-audience functions are pure and some deep-copy their inputs with
	// structuredClone — which throws DataCloneError on Svelte's $state proxies. Every value that
	// crosses into them must be unproxied first; the result is committed wholesale, so nothing
	// reactive is lost by snapshotting.
	const snapPage = (p: BrandingPage) => $state.snapshot(p) as BrandingPage;
	function selectPage(id: string) {
		selectedPageId = id;
		selectedBlockId = null; // block ids are page-scoped
	}
	function setAudienceTab(tab: AudienceTab) {
		audienceTab = tab;
		selectedBlockId = null; // ids persist across tabs but the selected block may not
		// A gentle nudge, not a lock: picking an audience tab suggests the matching persona so the
		// preview shows the layout being edited; the firm can still preview any persona.
		if (tab !== 'default' && persona.audience !== tab) {
			personaKey = tab === 'organisation' ? 'trust300' : 'individual2';
		}
	}
	function updateLayout(blocks: BrandingBlock[]) {
		if (selectedPage) commitPage(setLayout(snapPage(selectedPage), audienceTab, blocks));
	}
	function updateBlockSettings(settings: Record<string, unknown>) {
		if (!selectedPage || !selectedBlock) return;
		updateLayout(currentLayout.map((b) => (b.id === selectedBlock.id ? { ...b, settings } : b)));
	}
	function duplicateSelected() {
		if (!selectedPage || !selectedBlock?.id) return;
		const next = duplicateBlock(
			$state.snapshot(currentLayout) as BrandingBlock[],
			selectedBlock.id
		);
		updateLayout(next);
		// The copy sits right after the original — select it so the inspector follows the action.
		const i = next.findIndex((b) => b.id === selectedBlock.id);
		selectedBlockId = next[i + 1]?.id ?? null;
	}
	function removeSelected() {
		if (!selectedPage || !selectedBlock) return;
		updateLayout(currentLayout.filter((b) => b.id !== selectedBlock.id));
		selectedBlockId = null;
	}

	// ── Page presets ────────────────────────────────────────────────────────────
	// The rail's "Start from a preset…" picker. Choosing one replaces the WHOLE working page set,
	// so it always confirms first (the repo's ConfirmDialog, destructive — the current layout is
	// lost). The select's value is transient: it re-arms to the placeholder whenever the dialog
	// closes, confirmed or not, so the control reads as an action, not a stored setting.
	let presetKey = $state('');
	let pendingPreset = $state<PagePreset | null>(null);
	let presetConfirmOpen = $state(false);
	function pickPreset(key: string) {
		const preset = presetByKey(key);
		if (!preset) return;
		pendingPreset = preset;
		presetConfirmOpen = true;
	}
	$effect(() => {
		if (!presetConfirmOpen && presetKey) presetKey = '';
	});
	function applyPagePreset() {
		if (!pendingPreset) return;
		// Deep-clone: PAGE_PRESETS is shared module data and the working set is mutable $state.
		commitPages(structuredClone(pendingPreset.pages) as BrandingPage[]);
		// Land on the preset's first page with nothing selected — the old selection's ids may be gone.
		selectedPageId = pendingPreset.pages[0]?.id ?? null;
		selectedBlockId = null;
		audienceTab = 'default';
	}

	const previewPortal = $derived<'client' | 'platform'>(
		portal === 'client' ? 'client' : 'platform'
	);
	const portalLabel = $derived(portal === 'client' ? 'Client portal' : 'Adviser portal');

	// Both portals' sans, mono AND display families: naming a font var without fetching the file
	// leaves the preview on the fallback stack, so the studio would show no change for a setting
	// that does work in the real portal (brandingHead loads all three). Same trap the sans font had.
	const fontHrefs = $derived(
		[
			...new Set([
				previewBranding.font,
				drafts.advisor.font,
				previewBranding.font_mono,
				drafts.advisor.font_mono,
				previewBranding.font_display,
				drafts.advisor.font_display
			])
		]
			.map((f) => googleFontHref(f))
			.filter((h): h is string => h !== null)
	);

	// Dropdown item lists. '' is the "leave it to the design system" choice, not a missing value —
	// SelectMenu shows its label rather than the placeholder, which is what we want here.
	const MODE_ITEMS = [
		{ value: 'auto', label: 'Auto (follow device)' },
		{ value: 'light', label: 'Light' },
		{ value: 'dark', label: 'Dark' }
	];
	// The three font pickers each offer their curated list plus "Other…" for any Google Fonts
	// family (FontPicker). '' ⇒ null: the sans and mono fall to the design system's defaults, and
	// --font-display keeps its theme.css default, var(--font-sans).

	// ── curated generation ────────────────────────────────────────────────────
	function regenerate() {
		applyTheme(
			active,
			generateTheme(seeds[portal].primary, seeds[portal].accent, styleKey[portal])
		);
	}
	function setSeed(which: 'primary' | 'accent', hex: string) {
		seeds[portal][which] = hex;
		if (portal === 'client') regenerate();
		else applyAccents(active, seeds.advisor.primary, seeds.advisor.accent);
	}
	function setStyle(k: ThemeStyleKey) {
		styleKey[portal] = k;
		regenerate();
	}
	// The hero swatch shows the explicit colour, or the deep shade the portal will derive.
	const heroShown = $derived(
		colorToHex(active.hero_color) ??
			colorToHex(
				heroInk(scheme === 'dark' ? active.colors?.dark?.primary : active.colors?.light?.primary)
			) ??
			'#243044'
	);
	// Typed hex is held locally until it is a whole #rrggbb (or emptied), so a half-typed
	// value is not snapped back to the committed colour on each keystroke.
	let heroDraft = $state('');
	let heroDrafting = $state(false);
	function setHero(raw: string) {
		heroDrafting = true;
		heroDraft = raw;
		const v = raw.trim();
		if (v === '') {
			active.hero_color = null;
			return;
		}
		if (/^#[0-9a-fA-F]{6}$/.test(v)) active.hero_color = v.toLowerCase();
	}
	function applyPreset(preset: ThemePreset) {
		seeds[portal] = { primary: preset.primary, accent: preset.accent };
		styleKey[portal] = preset.style;
		active.mode = preset.mode;
		if (preset.mode !== 'auto') scheme = preset.mode;
		regenerate();
	}

	// ── helpers for the misc controls ─────────────────────────────────────────
	function ensureGlass(b: Branding): BrandingGlass {
		b.glass ??= {};
		return b.glass;
	}
	function ensureLogin(b: Branding) {
		b.login ??= {};
		return b.login;
	}
	function ensureSupport(b: Branding) {
		b.support ??= {};
		return b.support;
	}
	// ── card designs ──────────────────────────────────────────────────────────
	// One row per product type the firm has enabled. A row matching its product default (no
	// artwork) is removed from the record, so an untouched design keeps tracking the defaults
	// rather than freezing today's.
	const cardRows = $derived(productTypes);
	function setCardDesign(type: string, patch: Partial<CardDesign>) {
		const current = designFor(type, active.card_designs);
		const next: CardDesign = { ...current, ...patch };
		const fallback = DEFAULT_CARD_DESIGNS[type];
		if (!next.color) delete next.color;
		const designs = { ...(active.card_designs ?? {}) };
		// A custom colour is itself a design, so it keeps the row even when the finish matches
		// the product default. Clearing it returns the card to that preset.
		if (!next.art_url && !next.color && fallback && next.finish === fallback.finish)
			delete designs[type];
		else designs[type] = next;
		active.card_designs = Object.keys(designs).length ? designs : null;
	}
	function setCardColor(type: string, raw: string) {
		const v = raw.trim();
		if (v === '') {
			setCardDesign(type, { color: null });
			return;
		}
		// A whole hex only — a half-typed value is not a colour, and the field snaps back to the
		// committed one. The picker always supplies a full #rrggbb.
		if (/^#[0-9a-fA-F]{3}$/.test(v) || /^#[0-9a-fA-F]{6}$/.test(v))
			setCardDesign(type, { color: v.toLowerCase() });
	}
	let uploadingCardType = $state<string | null>(null);
	async function uploadCardArt(type: string, event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file || !onupload) return;
		uploadingCardType = type;
		try {
			setCardDesign(type, { art_url: await onupload(portal, 'card-art', file) });
		} finally {
			uploadingCardType = null;
			input.value = '';
		}
	}
	// The swatches sit in the studio's own chrome, so they borrow the design's brand inline to
	// show the firm's finish rather than Graphene's.
	const swatchBrand = $derived(active.colors?.[scheme]?.brand ?? active.colors?.light?.brand);
	const swatchStyle = (type: string) =>
		(swatchBrand ? `--brand:${swatchBrand};` : '') + accountFace(type, active.card_designs);

	function radiusRem(): number {
		const m = /^(\d{1,3}(?:\.\d+)?)rem$/.exec(active.radius ?? '');
		if (m?.[1]) return Math.min(2, Math.max(0, parseFloat(m[1])));
		return 0.75;
	}

	// The ledger finish owns the surface material: sharedDecls emits neither the firm's radius nor
	// its glass knobs with it (client portal only — the adviser portal is a fixed surface and
	// never takes the finish). The radius + Material controls disable under it rather than
	// advertise settings with no effect.
	const ledgerActive = $derived(portal === 'client' && active.finish === 'ledger');
	const glassOn = $derived(active.glass?.enabled !== false);
	const glassOpacity = $derived(active.glass?.opacity ?? 55);
	const glassBlur = $derived(active.glass?.blur ?? 20);
	const glassSheen = $derived(active.glass?.sheen ?? 60);

	function toPlain<T>(value: T): T {
		// JSON is the wire format the action POSTs. structuredClone($state.snapshot(...)) has
		// omitted a `pages` property assigned onto an existing draft, so Save toasted success
		// while the PUT echoed the previous composition.
		return JSON.parse(JSON.stringify(value)) as T;
	}

	function save() {
		let client = toPlain($state.snapshot(drafts.client) as Branding);
		if (pagesDirty && workingPages) {
			client = withWorkingPages(
				client,
				toPlain($state.snapshot(workingPages) as BrandingPage[]),
				selectedTemplateId
			);
		}
		onsave?.({
			name: draftName,
			client,
			platform: staffPortalBranding(toPlain($state.snapshot(drafts.advisor) as Branding))
		});
	}

	let uploadingKind = $state<UploadKind | null>(null);
	async function handleUpload(kind: UploadKind, event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file || !onupload) return;
		uploadingKind = kind;
		try {
			const url = await onupload(portal, kind, file);
			if (kind === 'logo') sharedBranding.logo_url = url;
			else if (kind === 'logo-dark') sharedBranding.logo_dark_url = url;
			else if (kind === 'icon') sharedBranding.icon_url = url;
			else if (kind === 'favicon') sharedBranding.favicon_url = url;
		} finally {
			uploadingKind = null;
			input.value = '';
		}
	}

	/** Uploads fall back to a `data:` URI with no bucket configured. The portals render one; every mail client blocks it, so an inlined asset is unusable in email. */
	const isInlineImage = (url: string | null | undefined): boolean =>
		typeof url === 'string' && url.trim().startsWith('data:');

	const ASSET_FIELDS: {
		kind: UploadKind;
		label: string;
		prop: keyof Branding;
		help: string;
		/** Email reads this asset. Not `logo_dark_url`/`favicon_url` — no colour-scheme swap, no tab. */
		emailUse?: boolean;
	}[] = [
		{
			kind: 'logo',
			label: 'Logo',
			prop: 'logo_url',
			help: 'Headers and the login screen.',
			emailUse: true
		},
		{
			kind: 'logo-dark',
			label: 'Dark-mode logo',
			prop: 'logo_dark_url',
			help: 'Optional — used in dark mode.'
		},
		{
			kind: 'icon',
			label: 'Icon (symbol)',
			prop: 'icon_url',
			help: 'Used when Logo style is “Icon” — a compact symbol, no text.',
			emailUse: true
		},
		{ kind: 'favicon', label: 'Favicon', prop: 'favicon_url', help: 'The browser-tab icon.' }
	];
	function assetUrl(prop: keyof Branding): string {
		const v = sharedBranding[prop];
		return typeof v === 'string' ? v : '';
	}
	function setAssetUrl(prop: keyof Branding, value: string) {
		// @ts-expect-error — prop is constrained to the three url fields by ASSET_FIELDS.
		sharedBranding[prop] = value.trim() || null;
	}

	const isPresetActive = (p: ThemePreset) =>
		seeds[portal].primary.toLowerCase() === p.primary.toLowerCase() &&
		seeds[portal].accent.toLowerCase() === p.accent.toLowerCase() &&
		styleKey[portal] === p.style;
</script>

<svelte:head>
	{#each fontHrefs as href (href)}
		<link rel="stylesheet" {href} />
	{/each}
</svelte:head>

<div class="space-y-5">
	<!-- ── Toolbar ─────────────────────────────────────────────────────────────── -->
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div class="flex flex-wrap items-center gap-3">
			<Segmented
				bind:value={portal}
				options={[
					{ value: 'client', label: 'Client' },
					{ value: 'advisor', label: 'Adviser' }
				]}
			/>
			<Segmented
				bind:value={scheme}
				options={[
					{ value: 'light', label: 'Light' },
					{ value: 'dark', label: 'Dark' }
				]}
			/>
			{#if portal === 'client'}
				<Segmented
					bind:value={view}
					options={[
						{ value: 'design', label: 'Design' },
						{ value: 'layout', label: 'Pages' }
					]}
				/>
			{/if}
		</div>
		<div class="flex items-center gap-3">
			{#if error}<span class="text-destructive text-sm">{error}</span>{/if}
			{#if saved}<span class="text-success-strong text-sm">Saved.</span>{/if}
			{#if canManage}
				<Button onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save branding'}</Button>
			{/if}
		</div>
	</div>

	{#if portal === 'client'}
		{#if classifications !== undefined}
			<ClassificationCatalogue
				{classifications}
				designs={clientTemplates}
				{canManage}
				canAssignDesign={canManage && canEditPages}
				{saving}
				onSave={onSaveClassification}
				onDelete={onDeleteClassification}
				onAssignDesign={assignBookDesign}
			/>
		{/if}
		{#if groups !== undefined}
			<GroupCatalogue
				{groups}
				designs={clientTemplates}
				{canManage}
				canAssignDesign={canManage && canEditPages}
				{saving}
				onSave={onSaveGroup}
				onDelete={onDeleteGroup}
				onAssignDesign={assignGroupDesign}
			/>
		{/if}
		<Card.Root>
			<Card.Header>
				<div class="flex flex-wrap items-start justify-between gap-3">
					<div class="space-y-1.5">
						<Card.Title>Client portal design sets</Card.Title>
						<Card.Description>
							Give each client book or group its own pages and colour scheme.
						</Card.Description>
					</div>
					{#if canManage}
						<div class="flex gap-2">
							{#if selectedTemplate}
								<Button variant="outline" size="sm" onclick={copyTemplate}>Duplicate</Button>
							{/if}
							<Button
								size="sm"
								onclick={createTemplate}
								disabled={clientTemplates.length >= MAX_PORTAL_TEMPLATES}
							>
								{clientTemplates.length === 0 ? 'Create first design' : 'New design'}
							</Button>
						</div>
					{/if}
				</div>
			</Card.Header>
			<Card.Content class="grid gap-4 lg:grid-cols-[minmax(12rem,0.8fr)_minmax(16rem,1.2fr)]">
				<div class="space-y-1.5">
					<Label for="portal-design-set">Design set</Label>
					<SelectMenu
						id="portal-design-set"
						value={selectedTemplateId ?? '__legacy'}
						items={clientTemplates.length > 0
							? clientTemplates.map((template) => ({
									value: template.id,
									label: template.name || template.id
								}))
							: [{ value: '__legacy', label: 'Current portal design' }]}
						onValueChange={(value) => selectTemplate(value === '__legacy' ? null : value)}
					/>
				</div>
				{#if selectedTemplate}
					<div class="grid gap-4">
						<div class="space-y-1.5">
							<Label for="portal-design-name">Name</Label>
							<Input
								id="portal-design-name"
								value={selectedTemplate.name}
								disabled={!canManage}
								oninput={(event) =>
									replaceTemplate({
										...selectedTemplate,
										name: event.currentTarget.value.slice(0, 40)
									})}
							/>
						</div>
						<div class="flex flex-wrap items-center gap-3">
							<Switch
								checked={drafts.client.default_template_id === selectedTemplate.id}
								disabled={!canManage}
								aria-label="Use as the default design"
								onCheckedChange={(checked) => {
									if (checked) drafts.client.default_template_id = selectedTemplate.id;
								}}
							/>
							<div class="min-w-0 flex-1">
								<p class="text-sm font-medium">Default design</p>
								<p class="text-muted-foreground text-xs">
									Used when no client book is assigned to a design.
								</p>
							</div>
							{#if canManage}
								<Button variant="ghost" size="sm" onclick={() => (deleteTemplateOpen = true)}>
									Delete design
								</Button>
							{/if}
						</div>
					</div>
				{:else}
					<p class="text-muted-foreground self-end text-sm">
						This is the existing one-size-fits-all portal. Create a design set to assign pages and
						colours to a client book.
					</p>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}

	{#if view === 'layout'}
		<!-- ── Pages view: rail | builder + inspector | live preview ───────────────
		     The preview only earns its own column at 2xl: the host portals carry a sidebar, so at
		     lg the CONTENT area is far narrower than the viewport and three fixed-ish tracks would
		     crush the builder to nothing. Below 2xl the preview flows full-width underneath. -->
		{#if !canEditPages}
			<!-- Read-only composition: the caller lacks pages:write, so the rail/builder/inspector
			     render without their edit affordances. Quiet on purpose — the preview is still the
			     point of being here. -->
			<p class="text-muted-foreground text-sm">
				You can view how the client portal's pages are composed, but changing them needs the
				“Compose portal pages” permission.
			</p>
		{/if}
		<div
			class="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)] 2xl:grid-cols-[16rem_minmax(0,1fr)_28rem]"
		>
			<Card.Root class="lg:self-start">
				<Card.Header>
					<Card.Title>Pages</Card.Title>
					<Card.Description>Drag to reorder. The menu follows this order.</Card.Description>
					{#if pagesEditable}
						<Select.Root type="single" bind:value={presetKey} onValueChange={pickPreset}>
							<Select.Trigger size="sm" class="mt-1 w-full" aria-label="Start from a preset">
								<span class="text-muted-foreground text-xs">Start from a preset…</span>
							</Select.Trigger>
							<Select.Content class="w-72">
								{#each PAGE_PRESETS as preset (preset.key)}
									<Select.Item value={preset.key} label={preset.label}>
										<span class="flex min-w-0 flex-col gap-0.5 py-0.5 whitespace-normal">
											<span class="text-sm font-medium">{preset.label}</span>
											<span class="text-muted-foreground text-xs leading-snug">
												{preset.description}
											</span>
										</span>
									</Select.Item>
								{/each}
							</Select.Content>
						</Select.Root>
					{/if}
				</Card.Header>
				<Card.Content>
					<PagesRail
						pages={pagesWorking}
						selectedId={selectedPageId}
						canManage={pagesEditable}
						onchange={commitPages}
						onselect={selectPage}
					/>
				</Card.Content>
			</Card.Root>

			<div class="min-w-0 space-y-4">
				<div class="flex flex-wrap items-center gap-3">
					<Segmented
						value={audienceTab}
						onValueChange={(v) => setAudienceTab(v as AudienceTab)}
						options={[
							{ value: 'default', label: 'Default' },
							{ value: 'individual', label: 'Individuals' },
							{ value: 'organisation', label: 'Trusts & companies' }
						]}
					/>
					<div class="ml-auto flex items-center gap-2">
						<span class="text-muted-foreground text-xs">Preview as</span>
						<SelectMenu
							size="sm"
							class="w-44"
							value={personaKey}
							items={PERSONAS.map((p) => ({ value: p.key, label: p.label }))}
							onValueChange={(v) => (personaKey = v as Persona['key'])}
						/>
					</div>
				</div>

				{#if selectedPage}
					{#if editAudience && !audienceCustomised}
						<!-- This audience follows the page's default layout until explicitly forked. -->
						<div class="rounded-xl border border-dashed p-8 text-center">
							<Badge variant="outline">Following default</Badge>
							<p class="text-muted-foreground mx-auto mt-2 max-w-sm text-sm">
								{AUDIENCE_LABEL[editAudience]} see ‘{selectedPage.label}’s default layout. Customise
								it to arrange this page differently for them.
							</p>
							{#if pagesEditable}
								<Button
									variant="outline"
									class="mt-4"
									onclick={() => commitPage(customise(snapPage(selectedPage), editAudience))}
								>
									Customise for this audience
								</Button>
							{/if}
						</div>
					{:else}
						<Card.Root>
							<Card.Header>
								<div class="flex flex-wrap items-center justify-between gap-2">
									<Card.Title>{selectedPage.label} layout</Card.Title>
									{#if editAudience && audienceCustomised}
										<div class="flex items-center gap-2">
											<Badge variant="secondary">Customised</Badge>
											{#if pagesEditable}
												<Button variant="ghost" size="sm" onclick={() => (revertOpen = true)}>
													Revert to default
												</Button>
											{/if}
										</div>
									{/if}
								</div>
								<Card.Description>
									Drag a block by its handle to move it, drag the corner (or use ±) to resize,
									toggle to show or hide, and click a block to edit its settings. Tiles are drawn at
									the heights the preview is actually rendering; H sets a minimum, and a block
									taller than it keeps its own height.
								</Card.Description>
							</Card.Header>
							<Card.Content>
								<LayoutBuilder
									layout={currentLayout}
									canManage={pagesEditable}
									metrics={gridMetrics}
									selectedId={selectedBlockId}
									onselect={(id) => (selectedBlockId = id)}
									onchange={updateLayout}
								/>
							</Card.Content>
						</Card.Root>
					{/if}

					{#if selectedBlock}
						<Card.Root>
							<Card.Content>
								<BlockInspector
									block={selectedBlock}
									canManage={pagesEditable}
									{persona}
									pageSlug={selectedPage.slug}
									onchange={updateBlockSettings}
									onduplicate={duplicateSelected}
									onremove={removeSelected}
								/>
							</Card.Content>
						</Card.Root>
					{/if}
				{/if}
			</div>

			<div class="space-y-2 lg:col-span-2 2xl:col-span-1 2xl:sticky 2xl:top-6 2xl:self-start">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<p class="text-muted-foreground text-xs">
						Client portal preview ({scheme}), shown as
						<span class="text-foreground font-medium">{persona.label}</span>.
					</p>
					<Segmented
						class="text-xs"
						value={device}
						options={DEVICE_OPTIONS}
						onValueChange={(v) => (device = v as PreviewDevice)}
					/>
				</div>
				<ShellPreview
					branding={previewBranding}
					{scheme}
					portal="client"
					name={draftName}
					pages={pagesWorking}
					activeSlug={selectedPage?.slug ?? 'home'}
					{persona}
					audience={previewAudience}
					{device}
					{fontHrefs}
					onmeasure={reportMetrics}
				/>
			</div>
		</div>

		{#if pendingPreset}
			<ConfirmDialog
				bind:open={presetConfirmOpen}
				title={`Replace your pages with the ‘${pendingPreset.label}’ preset?`}
				description="Your current layout will be lost."
				confirmLabel="Replace pages"
				destructive
				onconfirm={applyPagePreset}
			/>
		{/if}

		{#if selectedPage && editAudience}
			<ConfirmDialog
				bind:open={revertOpen}
				title="Revert to the default layout?"
				description={`The ${AUDIENCE_LABEL[editAudience].toLowerCase()} layout for ‘${selectedPage.label}’ is discarded and the page follows its default again.`}
				confirmLabel="Revert"
				destructive
				onconfirm={() => {
					if (selectedPage && editAudience) {
						commitPage(revert(snapPage(selectedPage), editAudience));
						selectedBlockId = null;
					}
				}}
			/>
		{/if}
	{:else}
		<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_23rem]">
			<!-- ── Live real-shell preview ─────────────────────────────────────────── -->
			<div class="space-y-2 lg:sticky lg:top-6 lg:self-start">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<p class="text-muted-foreground text-xs">
						<span class="text-foreground font-medium">{portalLabel}</span> preview ({scheme}) with
						example data.
					</p>
					<Segmented
						class="text-xs"
						value={device}
						options={DEVICE_OPTIONS}
						onValueChange={(v) => (device = v as PreviewDevice)}
					/>
				</div>
				<!-- `pages` is snapshotted, never `active.pages` itself: materialiseWorkingPages
				     deep-clones with structuredClone, which THROWS on a $state proxy
				     (DataCloneError) -- and only in the BROWSER, since SSR has no proxies. Every
				     other clone site in this file snapshots first; this one did not, and it is the
				     DEFAULT view, so the whole studio died on hydration while rendering and
				     testing perfectly clean on the server. -->
				<ShellPreview
					branding={previewBranding}
					{scheme}
					portal={previewPortal}
					name={draftName}
					pages={portal === 'client'
						? materialiseWorkingPages(($state.snapshot(active) as Branding).pages)
						: null}
					{device}
					{fontHrefs}
				/>
			</div>

			<!-- ── Curated controls ────────────────────────────────────────────────── -->
			<div class="space-y-6">
				{#if portal === 'client'}
					<!-- Theme -->
					<Card.Root>
						<Card.Header>
							<Card.Title>Theme</Card.Title>
							<Card.Description>
								Start from a preset or pick your colours. A readable palette is generated for you.
							</Card.Description>
						</Card.Header>
						<Card.Content class="space-y-4">
							<!-- Preset gallery -->
							<div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
								{#each THEME_PRESETS as preset (preset.name)}
									<button
										type="button"
										disabled={!canManage}
										onclick={() => applyPreset(preset)}
										class={cn(
											'group flex flex-col items-stretch gap-1.5 rounded-lg border p-2 text-left transition-colors disabled:opacity-50',
											isPresetActive(preset)
												? 'border-primary ring-primary/30 ring-1'
												: 'hover:border-ring/50'
										)}
									>
										<span class="flex h-6 overflow-hidden rounded">
											<span class="flex-1" style="background:{preset.primary}"></span>
											<span class="flex-1" style="background:{preset.accent}"></span>
										</span>
										<span class="truncate text-[0.7rem] font-medium">{preset.name}</span>
									</button>
								{/each}
							</div>

							<!-- Brand + accent seeds -->
							<div class="grid grid-cols-2 gap-3">
								<div class="space-y-1.5">
									<Label>Brand colour</Label>
									<div class="flex items-center gap-2">
										<input
											type="color"
											aria-label="Brand colour"
											disabled={!canManage}
											value={seeds[portal].primary}
											oninput={(e) => setSeed('primary', e.currentTarget.value)}
											class="border-input size-9 shrink-0 cursor-pointer rounded-md border bg-transparent p-1"
										/>
										<Input
											value={seeds[portal].primary}
											disabled={!canManage}
											oninput={(e) => setSeed('primary', e.currentTarget.value)}
											class="font-mono text-xs"
										/>
									</div>
								</div>
								<div class="space-y-1.5">
									<Label>Accent</Label>
									<div class="flex items-center gap-2">
										<input
											type="color"
											aria-label="Accent colour"
											disabled={!canManage}
											value={seeds[portal].accent}
											oninput={(e) => setSeed('accent', e.currentTarget.value)}
											class="border-input size-9 shrink-0 cursor-pointer rounded-md border bg-transparent p-1"
										/>
										<Input
											value={seeds[portal].accent}
											disabled={!canManage}
											oninput={(e) => setSeed('accent', e.currentTarget.value)}
											class="font-mono text-xs"
										/>
									</div>
								</div>
							</div>

							<!-- Hero: colour + feel. Unset colour follows the brand (a deep shade), so the band
							     cannot repeat the buttons. -->
							<div class="space-y-1.5">
								<div class="flex items-center justify-between gap-2">
									<Label for="brand-hero">Hero</Label>
									{#if active.hero_color}
										<button
											type="button"
											class="text-muted-foreground text-[0.7rem] underline-offset-2 hover:underline disabled:opacity-50"
											disabled={!canManage}
											onclick={() => {
												heroDrafting = false;
												active.hero_color = null;
											}}
										>
											Follow the brand
										</button>
									{/if}
								</div>
								<div class="flex items-center gap-2">
									<input
										id="brand-hero"
										type="color"
										aria-label="Hero colour"
										disabled={!canManage}
										value={heroShown}
										oninput={(e) => {
											heroDrafting = false;
											active.hero_color = e.currentTarget.value;
										}}
										class="border-input size-9 shrink-0 cursor-pointer rounded-md border bg-transparent p-1"
									/>
									<Input
										value={heroDrafting ? heroDraft : (active.hero_color ?? '')}
										disabled={!canManage}
										placeholder="Follows the brand"
										class="font-mono text-xs"
										oninput={(e) => setHero(e.currentTarget.value)}
										onblur={() => (heroDrafting = false)}
									/>
								</div>
								<Segmented
									value={active.hero_feel ?? 'gradient'}
									disabled={!canManage}
									onValueChange={(v) =>
										(active.hero_feel = v === 'gradient' ? null : (v as 'solid' | 'luminous'))}
									options={[
										{ value: 'gradient', label: 'Gradient' },
										{ value: 'solid', label: 'Solid' },
										{ value: 'luminous', label: 'Luminous' }
									]}
								/>
								<p class="text-muted-foreground text-[0.7rem] leading-snug">
									The band behind the portfolio total. Left blank, it is a deep shade of the brand
									colour, so it stays distinct from the buttons.
								</p>
							</div>

							<!-- Style -->
							<div class="space-y-1.5">
								<Label>Style</Label>
								<Segmented
									value={styleKey[portal]}
									disabled={!canManage}
									onValueChange={(v) => setStyle(v as ThemeStyleKey)}
									options={THEME_STYLE_KEYS.map((k) => ({
										value: k,
										label: THEME_STYLES[k].label
									}))}
								/>
							</div>
						</Card.Content>
					</Card.Root>

					<!-- Appearance -->
					<Card.Root>
						<Card.Header>
							<Card.Title>Appearance</Card.Title>
							<Card.Description>Default scheme, corners, type, density and finish.</Card.Description
							>
						</Card.Header>
						<Card.Content class="space-y-4">
							<div class="space-y-1.5">
								<Label for="brand-mode">Default scheme</Label>
								<SelectMenu
									id="brand-mode"
									value={active.mode ?? 'auto'}
									disabled={!canManage}
									items={MODE_ITEMS}
									onValueChange={(v) => (active.mode = v as 'auto' | 'light' | 'dark')}
								/>
							</div>
							<div class="space-y-1.5">
								<div class="flex justify-between text-xs">
									<Label>Corner radius</Label>
									<span class="text-muted-foreground font-mono tabular-nums">
										{ledgerActive ? '0rem (set by Ledger)' : (active.radius ?? '0.75rem')}
									</span>
								</div>
								<input
									type="range"
									min="0"
									max="2"
									step="0.05"
									value={ledgerActive ? 0 : radiusRem()}
									disabled={!canManage || ledgerActive}
									oninput={(e) => (active.radius = `${e.currentTarget.value}rem`)}
									class="accent-primary h-2 w-full cursor-pointer"
									aria-label="Corner radius"
								/>
							</div>
							<div class="space-y-1.5">
								<Label for="brand-font">Font</Label>
								<FontPicker
									id="brand-font"
									value={active.font}
									options={FONT_OPTIONS}
									defaultLabel="System default (Inter)"
									disabled={!canManage}
									onchange={(next) => (active.font = next)}
								/>
							</div>
							<div class="space-y-1.5">
								<Label for="brand-font-mono">Monospace font</Label>
								<FontPicker
									id="brand-font-mono"
									value={active.font_mono}
									options={MONO_FONT_OPTIONS}
									defaultLabel="System default (monospace)"
									disabled={!canManage}
									onchange={(next) => (active.font_mono = next)}
								/>
								<p class="text-muted-foreground text-[0.7rem] leading-snug">
									Used for reference numbers, account IDs and tabular figures.
									<span class="font-mono">ABC-12345 · 1,234.56</span>
								</p>
							</div>
							<div class="space-y-1.5">
								<Label for="brand-font-display">Display font</Label>
								<FontPicker
									id="brand-font-display"
									value={active.font_display}
									options={DISPLAY_FONT_OPTIONS}
									defaultLabel="Same as body"
									disabled={!canManage}
									onchange={(next) => (active.font_display = next)}
								/>
								<p class="text-muted-foreground text-[0.7rem] leading-snug">
									Used for large figures, such as portfolio totals.
								</p>
							</div>
							<div class="space-y-1.5">
								<Label>Density</Label>
								<!-- 'standard' is stored as null so an un-customised tenant's record stays clean
							     (and keeps tracking the design system's default scale). -->
								<Segmented
									value={active.density ?? 'standard'}
									disabled={!canManage}
									onValueChange={(v) =>
										(active.density = v === 'standard' ? null : (v as 'comfortable' | 'compact'))}
									options={[
										{ value: 'comfortable', label: 'Comfortable' },
										{ value: 'standard', label: 'Standard' },
										{ value: 'compact', label: 'Compact' }
									]}
								/>
								<p class="text-muted-foreground text-[0.7rem] leading-snug">
									Comfortable suits clients who prefer larger text; Compact fits data-heavy books.
									Applies to this portal only.
								</p>
							</div>
							{#if portal === 'client'}
								<div class="space-y-1.5">
									<Label>Finish</Label>
									<!-- Client portal only: the adviser portal is a fixed surface — brandingHead
								     withholds the finish there exactly as it does the glass material, so
								     showing the control would advertise a setting with no effect (the same
								     trap the preview's surface:'fixed' handling exists to avoid).
								     'card' is stored as null so an un-customised tenant's record stays clean. -->
									<Segmented
										value={active.finish ?? 'card'}
										disabled={!canManage}
										onValueChange={(v) => (active.finish = v === 'card' ? null : 'ledger')}
										options={[
											{ value: 'card', label: 'Cards' },
											{ value: 'ledger', label: 'Ledger' }
										]}
									/>
									<p class="text-muted-foreground text-[0.7rem] leading-snug">
										Ledger strips the card chrome for an engraved, statement-like register. Pairs
										well with a display font.
									</p>
								</div>
							{/if}
						</Card.Content>
					</Card.Root>

					<!-- Material -->
					<Card.Root>
						<Card.Header class="flex-row items-center justify-between space-y-0">
							<div class="space-y-1.5">
								<Card.Title>Material</Card.Title>
								<Card.Description>
									{ledgerActive
										? 'The Ledger finish uses open sections with fine rules.'
										: 'Use frosted glass or turn it off for solid surfaces.'}
								</Card.Description>
							</div>
							<Switch
								checked={glassOn && !ledgerActive}
								disabled={!canManage || ledgerActive}
								aria-label="Frosted glass"
								onCheckedChange={(v) => (ensureGlass(active).enabled = v)}
							/>
						</Card.Header>
						{#if glassOn && !ledgerActive}
							<Card.Content class="space-y-4">
								{#each [{ label: 'Opacity', v: glassOpacity, min: 20, max: 100, unit: '%', set: (n: number) => (ensureGlass(active).opacity = n) }, { label: 'Blur', v: glassBlur, min: 0, max: 40, unit: 'px', set: (n: number) => (ensureGlass(active).blur = n) }, { label: 'Sheen', v: glassSheen, min: 0, max: 100, unit: '%', set: (n: number) => (ensureGlass(active).sheen = n) }] as s (s.label)}
									<div class="space-y-1.5">
										<div class="flex justify-between text-xs">
											<Label>{s.label}</Label>
											<span class="text-muted-foreground tabular-nums">{s.v}{s.unit}</span>
										</div>
										<input
											type="range"
											min={s.min}
											max={s.max}
											step="1"
											value={s.v}
											disabled={!canManage}
											oninput={(e) => s.set(+e.currentTarget.value)}
											class="accent-primary h-2 w-full cursor-pointer"
											aria-label={s.label}
										/>
									</div>
								{/each}
							</Card.Content>
						{/if}
					</Card.Root>

					<!-- Card designs -->
					<Card.Root>
						<Card.Header>
							<Card.Title>Card designs</Card.Title>
							<Card.Description>
								How each product this firm offers is painted as an account card. Pick a preset,
								override it with a colour, or upload artwork.
							</Card.Description>
						</Card.Header>
						<Card.Content class="space-y-4">
							{#if productTypesUnavailable}
								<p class="text-muted-foreground text-sm">
									This firm's products could not be loaded, so its card designs can't be shown.
								</p>
							{:else if cardRows.length === 0}
								<p class="text-muted-foreground text-sm">
									This firm has no products enabled, so there are no cards to design.
								</p>
							{/if}
							{#each cardRows as product (product.type)}
								{@const design = designFor(product.type, active.card_designs)}
								<div class="flex items-start gap-3" data-card-design={product.type}>
									<div
										class="hero-surface flex h-12 w-20 shrink-0 items-end rounded-lg p-1.5 text-[0.65rem] leading-tight font-semibold"
										style={swatchStyle(product.type)}
										aria-hidden="true"
									>
										{product.type}
									</div>
									<div class="min-w-0 flex-1 space-y-2">
										{#if product.name !== product.type}
											<p class="text-sm font-medium">{product.name}</p>
										{/if}
										<Segmented
											size="sm"
											label={`${product.name} card finish`}
											value={design.finish ?? 'ink'}
											disabled={!canManage || !!design.color}
											onValueChange={(v) =>
												setCardDesign(product.type, { finish: v as CardFinish })}
											options={FINISHES.map((f) => ({ value: f, label: FINISH_LABELS[f] }))}
										/>
										<div class="flex flex-wrap items-center gap-2">
											<input
												type="color"
												aria-label={`${product.name} custom colour`}
												disabled={!canManage}
												value={colorToHex(design.color) ?? '#1e293b'}
												oninput={(e) =>
													setCardDesign(product.type, { color: e.currentTarget.value })}
												class={cn(
													'border-input size-7 shrink-0 cursor-pointer rounded-md border bg-transparent p-0.5',
													!design.color && 'opacity-40'
												)}
											/>
											<Input
												value={design.color ?? ''}
												disabled={!canManage}
												placeholder="Preset colour"
												aria-label={`${product.name} custom colour hex`}
												class="h-7 w-28 font-mono text-xs"
												oninput={(e) => setCardColor(product.type, e.currentTarget.value)}
											/>
											{#if design.color}
												<button
													type="button"
													class="text-muted-foreground text-[0.7rem] underline-offset-2 hover:underline disabled:opacity-50"
													disabled={!canManage}
													onclick={() => setCardDesign(product.type, { color: null })}
												>
													Use preset
												</button>
											{/if}
										</div>
										<div class="flex flex-wrap items-center gap-2">
											<label
												class={cn(
													'border-input bg-background inline-flex h-7 cursor-pointer items-center rounded-md border px-2.5 text-xs font-medium',
													(!canManage || uploading || uploadingCardType !== null) &&
														'pointer-events-none opacity-50'
												)}
											>
												{uploadingCardType === product.type
													? 'Uploading…'
													: design.art_url
														? 'Replace artwork'
														: 'Upload artwork'}
												<input
													type="file"
													accept="image/*"
													class="sr-only"
													disabled={!canManage || uploading || uploadingCardType !== null}
													onchange={(e) => uploadCardArt(product.type, e)}
												/>
											</label>
											{#if design.art_url}
												<Segmented
													size="sm"
													label={`${product.name} artwork text colour`}
													value={design.ink ?? 'light'}
													disabled={!canManage}
													onValueChange={(v) =>
														setCardDesign(product.type, { ink: v === 'dark' ? 'dark' : null })}
													options={[
														{ value: 'light', label: 'Light text' },
														{ value: 'dark', label: 'Dark text' }
													]}
												/>
												<Button
													variant="ghost"
													size="sm"
													disabled={!canManage}
													onclick={() => setCardDesign(product.type, { art_url: null, ink: null })}
												>
													Remove artwork
												</Button>
											{/if}
										</div>
									</div>
								</div>
							{/each}
						</Card.Content>
					</Card.Root>
				{:else}
					<Card.Root>
						<Card.Header>
							<Card.Title>Accents</Card.Title>
							<Card.Description>
								Ink for buttons, the active nav mark and focus rings. Paper, type and chrome stay
								the product look.
							</Card.Description>
						</Card.Header>
						<Card.Content class="space-y-4">
							<div class="grid grid-cols-2 gap-3">
								<div class="space-y-1.5">
									<Label>Primary</Label>
									<div class="flex items-center gap-2">
										<input
											type="color"
											aria-label="Primary colour"
											disabled={!canManage}
											value={seeds[portal].primary}
											oninput={(e) => setSeed('primary', e.currentTarget.value)}
											class="border-input size-9 shrink-0 cursor-pointer rounded-md border bg-transparent p-1"
										/>
										<Input
											value={seeds[portal].primary}
											disabled={!canManage}
											oninput={(e) => setSeed('primary', e.currentTarget.value)}
											class="font-mono text-xs"
										/>
									</div>
								</div>
								<div class="space-y-1.5">
									<Label>Accent</Label>
									<div class="flex items-center gap-2">
										<input
											type="color"
											aria-label="Accent colour"
											disabled={!canManage}
											value={seeds[portal].accent}
											oninput={(e) => setSeed('accent', e.currentTarget.value)}
											class="border-input size-9 shrink-0 cursor-pointer rounded-md border bg-transparent p-1"
										/>
										<Input
											value={seeds[portal].accent}
											disabled={!canManage}
											oninput={(e) => setSeed('accent', e.currentTarget.value)}
											class="font-mono text-xs"
										/>
									</div>
								</div>
							</div>
							<div class="space-y-1.5">
								<Label for="brand-mode-staff">Default scheme</Label>
								<SelectMenu
									id="brand-mode-staff"
									value={active.mode ?? 'auto'}
									disabled={!canManage}
									items={MODE_ITEMS}
									onValueChange={(v) => (active.mode = v as 'auto' | 'light' | 'dark')}
								/>
							</div>
						</Card.Content>
					</Card.Root>
				{/if}

				<!-- Identity -->
				<Card.Root>
					<Card.Header>
						<Card.Title>Identity</Card.Title>
						<Card.Description
							>Firm name (shared) and the {portalLabel.toLowerCase()}'s logos.</Card.Description
						>
					</Card.Header>
					<Card.Content class="space-y-5">
						<div class="space-y-1.5">
							<Label for="brand-name">Firm name</Label>
							<Input
								id="brand-name"
								bind:value={draftName}
								disabled={!canManage}
								placeholder="Acme Wealth"
							/>
						</div>
						<div class="space-y-1.5">
							<Label>Logo style</Label>
							<Segmented
								value={sharedBranding.logo_variant ?? 'wordmark'}
								disabled={!canManage}
								onValueChange={(v) => (sharedBranding.logo_variant = v as 'icon' | 'wordmark')}
								options={[
									{ value: 'wordmark', label: 'Wordmark' },
									{ value: 'icon', label: 'Icon' }
								]}
							/>
							<span class="text-muted-foreground block text-xs">
								Wordmark shows your full logo without extra firm-name text. Icon shows just the
								symbol.
							</span>
						</div>
						{#each ASSET_FIELDS as f (f.kind)}
							<div class="space-y-2">
								<Label>{f.label}</Label>
								<div class="flex items-start gap-3">
									<div
										class="bg-muted/50 flex size-14 shrink-0 items-center justify-center overflow-hidden rounded-md border"
									>
										{#if assetUrl(f.prop)}
											<img
												src={assetUrl(f.prop)}
												alt={`${f.label} preview`}
												class="max-h-full max-w-full object-contain"
											/>
										{:else}
											<span class="text-muted-foreground text-[0.65rem]">None</span>
										{/if}
									</div>
									<div class="min-w-0 flex-1 space-y-2">
										<Input
											value={assetUrl(f.prop)}
											disabled={!canManage}
											oninput={(e) => setAssetUrl(f.prop, e.currentTarget.value)}
											placeholder="https://…"
											class="font-mono text-xs"
										/>
										<label
											class={cn(
												'border-input bg-background inline-flex h-8 cursor-pointer items-center rounded-md border px-3 text-xs font-medium',
												(!canManage || uploading || uploadingKind !== null) &&
													'pointer-events-none opacity-50'
											)}
										>
											{uploadingKind === f.kind ? 'Uploading…' : 'Upload'}
											<input
												type="file"
												accept="image/*"
												class="sr-only"
												disabled={!canManage || uploading || uploadingKind !== null}
												onchange={(e) => handleUpload(f.kind, e)}
											/>
										</label>
										<span class="text-muted-foreground block text-xs">{f.help}</span>
										{#if f.emailUse && isInlineImage(assetUrl(f.prop))}
											<!-- The silence was the bug: `app/email/transport.py::masthead_image` correctly refuses a `data:` image and falls through to type, and nothing said so. Shown only on the assets email reads, and only when the value really is inlined. -->
											<span class="text-warning-strong block text-xs">
												This image may not appear in email. Use a publicly hosted image to show it
												everywhere.
											</span>
										{/if}
									</div>
								</div>
							</div>
						{/each}
					</Card.Content>
				</Card.Root>

				<!-- More: login + support -->
				<details class="bg-card rounded-xl border">
					<summary class="cursor-pointer px-6 py-4 text-sm font-semibold"
						>Login screen & support</summary
					>
					<div class="space-y-4 px-6 pb-6">
						<div class="space-y-1.5">
							<Label for="brand-tagline">Login tagline</Label>
							<Textarea
								id="brand-tagline"
								value={sharedBranding.login?.tagline ?? ''}
								disabled={!canManage}
								oninput={(e) =>
									(ensureLogin(sharedBranding).tagline = e.currentTarget.value.trim() || null)}
								placeholder="The workspace for your financial life."
								rows={2}
							/>
						</div>
						<div class="space-y-1.5">
							<Label for="brand-legal">Legal / footer text</Label>
							<Textarea
								id="brand-legal"
								value={sharedBranding.login?.legal ?? ''}
								disabled={!canManage}
								oninput={(e) =>
									(ensureLogin(sharedBranding).legal = e.currentTarget.value.trim() || null)}
								placeholder="© Acme Wealth Ltd. Authorised and regulated…"
								rows={2}
							/>
						</div>
						<div class="grid gap-3 sm:grid-cols-2">
							<div class="space-y-1.5">
								<Label for="brand-support-email">Support email</Label>
								<EmailInput
									id="brand-support-email"
									value={sharedBranding.support?.email ?? ''}
									disabled={!canManage}
									onchange={(v) => (ensureSupport(sharedBranding).email = v.trim() || null)}
									placeholder="help@acme.com"
								/>
							</div>
							<div class="space-y-1.5">
								<Label for="brand-support-phone">Support phone</Label>
								<!-- The shared phone field: letters are refused at the keystroke and a number
								     nobody could ring is named under the box, as on a client record. -->
								<PhoneNumberInput
									id="brand-support-phone"
									value={sharedBranding.support?.phone ?? ''}
									disabled={!canManage}
									onchange={(v) => (ensureSupport(sharedBranding).phone = v.trim() || null)}
									placeholder="+44 20 7946 0000"
								/>
							</div>
						</div>
						<div class="space-y-1.5">
							<Label for="brand-support-website">Website</Label>
							<Input
								id="brand-support-website"
								value={sharedBranding.support?.website ?? ''}
								disabled={!canManage}
								oninput={(e) =>
									(ensureSupport(sharedBranding).website = e.currentTarget.value.trim() || null)}
								placeholder="https://acme.com"
							/>
						</div>
					</div>
				</details>
			</div>
		</div>
	{/if}

	{#if selectedTemplate}
		<ConfirmDialog
			bind:open={deleteTemplateOpen}
			title={`Delete ‘${selectedTemplate.name || selectedTemplate.id}’?`}
			description="Its client books will fall back to the default design."
			confirmLabel="Delete design"
			destructive
			onconfirm={deleteTemplate}
		/>
	{/if}
</div>
