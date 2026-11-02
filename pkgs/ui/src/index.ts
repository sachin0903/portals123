export { cn } from './lib/utils';
export type {
	WithElementRef,
	WithoutChild,
	WithoutChildren,
	WithoutChildrenOrChild
} from './lib/utils';
// The development-time layout tripwires — overflow-audit.ts and touch-audit.ts, scheduled by
// layout-watch.ts.
export {
	auditOverflow,
	markOverflow,
	reportOverflow,
	watchOverflow,
	type OverflowFinding,
	type OverflowKind
} from './lib/overflow-audit';
export {
	auditTouchTargets,
	markTouchTargets,
	reportTouchTargets,
	type TouchFinding,
	type TouchKind
} from './lib/touch-audit';
export { watchLayout, type LayoutWatchOptions } from './lib/layout-watch';

// Singletons — flat exports (used directly, e.g. <Button>, <Input>).
export { Button, buttonVariants } from './lib/components/ui/button/index.js';
export { Input } from './lib/components/ui/input/index.js';
export { Label } from './lib/components/ui/label/index.js';
export { Badge, badgeVariants } from './lib/components/ui/badge/index.js';
export { FIELD_CLASS, FIELD_WRAPPER_CLASS } from './lib/field-shell';
export { Separator } from './lib/components/ui/separator/index.js';
export { Skeleton } from './lib/components/ui/skeleton/index.js';
export {
	default as Segmented,
	type SegmentedOption
} from './lib/components/ui/segmented/segmented.svelte';
export { default as Pagination } from './lib/components/ui/pagination/pagination.svelte';
export { Slider } from './lib/components/ui/slider/index.js';
export { Switch } from './lib/components/ui/switch/index.js';
export { Stepper, type Step, type StepState } from './lib/components/ui/stepper/index.js';
// Light/dark/system switcher. Pairs with the `handle` hook + `%theme.attrs%`/`%theme.head%` in
// app.html, which server-render the scheme (see @graphene/auth theme.ts).
// `colourScheme.current` is the read side of the same switcher — the `.dark` class observed
// reactively. It is the ONLY sanctioned way to branch on the live scheme in script (never
// `matchMedia`, never mode-watcher, both of which track the OS rather than the app).
export { ThemeToggle, colourScheme } from './lib/components/ui/theme-toggle/index.js';
export { Checkbox } from './lib/components/ui/checkbox/index.js';
export {
	AddressLookup,
	formatAddress,
	normaliseAddress,
	type Address,
	type AddressSuggestion,
	type PostcoderAddress
} from './lib/components/ui/address-lookup/index.js';
export {
	BankAccountCheck,
	BankAccountCheckStatus,
	bankDigits,
	bankPairComplete,
	type BankCheckResult,
	type BankCheckStatus
} from './lib/components/ui/bank-account-check/index.js';
export { MultiSelect } from './lib/components/ui/multi-select/index.js';
export type { MultiSelectOption } from './lib/components/ui/multi-select/index.js';
export { RolePicker } from './lib/components/ui/role-picker/index.js';
export type { RolePickerOption } from './lib/components/ui/role-picker/index.js';
export { MemberDirectory } from './lib/components/ui/member-directory/index.js';
export type { MemberDirectoryRow } from './lib/components/ui/member-directory/index.js';
export { isAwaitingInvite, isSuspendedMember } from './lib/components/ui/member-directory/index.js';
export {
	ActivityHeatmap,
	AuthProfile,
	LiveSessionList
} from './lib/components/auth-profile/index.js';
export type {
	ActivityDay,
	AuthMembership,
	AuthProfileData,
	AuthSubjectAccess,
	LiveSessionRow
} from './lib/components/auth-profile/index.js';
export { RangeCalendar } from './lib/components/ui/range-calendar/index.js';
export { RecordPicker } from './lib/components/ui/record-picker/index.js';
export type { RecordOption } from './lib/components/ui/record-picker/index.js';
export { Combobox } from './lib/components/ui/combobox/index.js';
export type { ComboboxOption } from './lib/components/ui/combobox/index.js';
export { DataTable } from './lib/components/ui/data-table/index.js';
export { RecordId } from './lib/components/ui/record-id/index.js';
// For a PAGINATED caller that must sort the whole population before slicing it — see the note
// in data-table/index.ts.
export { defaultSort, sortRows } from './lib/components/ui/data-table/index.js';
export {
	holdingClass,
	holdingColumns,
	holdingMatches,
	type HoldingsFilter
} from './lib/components/investment/holdings-view.js';
export type {
	DataTableColumn,
	DataTableGroupKey,
	DataTableSort,
	SortDirection,
	SortValue
} from './lib/components/ui/data-table/index.js';
export { Money } from './lib/components/ui/money/index.js';
export { default as MoneyInput } from './lib/components/ui/money-input/money-input.svelte';

// The inspector controls, behind the shared style rail BOTH GrapesJS editors mount
// (`components/grapes/style-panel`) — the one that replaced GrapesJS's own HTML: native selects, a
// native colour input, Spectrum's popover, all against docs/design-language.md's opening law. They
// are general in themselves — a labelled row, a colour, a length, and a four-sided length — so they
// live in `components/ui` and are exported rather than inside either editor, because a primitive
// that is missing goes in the shared package.
export { default as ColorInput } from './lib/components/ui/color-input/color-input.svelte';
export {
	type Hsv,
	clamp01,
	hexToHsv,
	hsvToHex,
	isHex,
	isIndirectColour,
	normaliseHex,
	padPosition,
	swatchInk
} from './lib/components/ui/color-input/colour.js';
export { default as InspectorSector } from './lib/components/ui/inspector-sector/inspector-sector.svelte';
export { default as UnitInput } from './lib/components/ui/unit-input/unit-input.svelte';
export {
	type Dimension,
	defaultUnit,
	joinDimension,
	splitDimension
} from './lib/components/ui/unit-input/dimension.js';
export { default as BoxSides } from './lib/components/ui/box-sides/box-sides.svelte';
export {
	type BoxPart,
	type PartValues,
	type Side,
	SIDE_ORDER,
	SIDE_PARTS,
	allSides,
	anySideSet,
	commonSide,
	describeSides,
	firstSide,
	sidesLinked
} from './lib/components/ui/box-sides/box-sides.js';
export { default as PropertyRow } from './lib/components/ui/property-row/property-row.svelte';
export { Textarea } from './lib/components/ui/textarea/index.js';
export { Toaster } from './lib/components/ui/sonner/index.js';
// The imperative half of the toast pattern — re-exported so apps never depend on svelte-sonner
// directly (pnpm's strict node_modules would reject the undeclared import anyway).
export { toast } from 'svelte-sonner';
export { ConfirmDialog } from './lib/components/ui/confirm-dialog/index.js';
export { ViewAsBanner } from './lib/components/ui/view-as-banner/index.js';
export { default as EmptyState } from './lib/components/ui/empty-state/empty-state.svelte';
export { SectionHeading } from './lib/components/ui/section-heading/index.js';
export {
	SelectMenu,
	type SelectMenuOption,
	type SelectMenuGroup,
	type SelectMenuItems
} from './lib/components/ui/select-menu/index.js';
export {
	StatusBadge,
	statusBadgeVariants,
	type StatusTone
} from './lib/components/ui/status-badge/index.js';
// A rating on the green→amber→red ramp. Beside StatusBadge because it is the same pill in the same
// tables; separate because its colour is interpolated per value rather than looked up from a tone.
export {
	RiskBadge,
	readRisk,
	riskMix,
	type RiskStep
} from './lib/components/ui/risk-badge/index.js';
// An ISO 4217 code with its country's flag as a disc on the left — the currency column's pill.
export { CurrencyBadge } from './lib/components/ui/currency-badge/index.js';
export { currencyCountry, currencyFlag } from './lib/currency-flags.js';
// Copy a short machine value (an id, a key, a correlation reference) with the tick that confirms it.
// One control because the same block had been written four times and already behaved three ways.
export { CopyButton } from './lib/components/ui/copy-button/index.js';
export { DownloadLink } from './lib/components/ui/download-link/index.js';
export { DocumentViewer } from './lib/components/ui/document-viewer/index.js';
// Photos large, in a pop-up, with arrows between them; a PDF scan goes to DocumentViewer.
export { ImageViewer, type ViewerImage } from './lib/components/ui/image-viewer/index.js';
// The pdf.js renderer behind DocumentViewer's PDF branch. Exported in its own right because the
// template surfaces render a preview with no document record around it.
export { PdfViewer, PreviewSpinner } from './lib/components/ui/pdf-viewer/index.js';
export { DocumentPreviewSheet } from './lib/components/ui/document-preview-sheet/index.js';
export { LoadErrorBanner } from './lib/components/ui/load-error-banner/index.js';
// Streamed page data: the placeholder until the first answer lands, then the content — and the
// last answer KEPT on screen while the next is fetched. See streamed.svelte for why this is not
// `{#await}`, and listing-skeleton for the placeholder the book surfaces use.
export { Streamed } from './lib/components/ui/streamed/index.js';
export { ListingSkeleton } from './lib/components/ui/listing-skeleton/index.js';
export { RecordSkeleton } from './lib/components/ui/record-skeleton/index.js';
// One photo from the device camera (document or face), with an upload fallback; a document may
// also be a PDF scan, shown as a file card. `checkDocumentPhoto` warns when an identity document
// photo does not look like the document and side it is meant to be.
export {
	CameraCapture,
	CapturedFileCard,
	checkDocumentPhoto,
	isPdfFile,
	prepareDocumentCheck,
	type CameraCaptureMode,
	type CameraCaptureStart,
	type CameraProblem,
	type DocumentKind,
	type DocumentSide
} from './lib/components/ui/camera-capture/index.js';
// New identity photos for a client, uploaded on their behalf by Graphene operations (admin) or the
// firm's advisers (platform). One dialog for both; each portal names its own form action.
export {
	IdentityUploadDialog,
	IdentityUploadButton,
	IDENTITY_DOCUMENT_TYPES,
	IDENTITY_PHOTO_FIELDS,
	documentTypeLabel,
	uploadSlots,
	type IdentityDocumentType,
	type IdentityPhotoField,
	type IdentityPhotoSlot,
	type IdentityUpload
} from './lib/components/compliance/identity-upload/index.js';
// A check's photos and its analysis under them, shown alike on the AML console and the firm's
// compliance tab.
export {
	IdentityPhotoGrid,
	IdentityAnalysisView,
	OUTCOME_STYLES,
	hasAnalysis,
	outcomeKind,
	scorePercent,
	type IdentityPhoto,
	type IdentityAnalysis,
	type IdentityOutcome,
	type OutcomeKind
} from './lib/components/compliance/identity-photos/index.js';
export {
	AmlMatchPhotos,
	AmlMatchProfile,
	addressLine,
	bornLine,
	countryReasons,
	groupByType,
	linkHost,
	matchScore,
	matchSubtitle,
	partialDate,
	roleDates,
	type AmlMatchProfileData,
	type MatchAddress,
	type MatchAssociation,
	type MatchCountry,
	type MatchDate,
	type MatchName,
	type MatchReference,
	type MatchRole
} from './lib/components/compliance/aml-match/index.js';
export {
	FileDropzone,
	dropzoneVariants,
	DEFAULT_ACCEPT,
	DEFAULT_MAX_BYTES
} from './lib/components/ui/file-dropzone/index.js';

// Card + Alert — flat prefixed aliases (kept so existing pages need no change).
export {
	Card,
	CardHeader,
	CardTitle,
	CardDescription,
	CardContent,
	CardFooter,
	CardAction
} from './lib/components/ui/card/index.js';
export {
	Alert,
	AlertTitle,
	AlertDescription,
	alertVariants
} from './lib/components/ui/alert/index.js';

// Compound components — namespaced (shadcn-svelte convention: <Dialog.Root>, etc.).
export * as Avatar from './lib/components/ui/avatar/index.js';
export * as Breadcrumb from './lib/components/ui/breadcrumb/index.js';
export * as Collapsible from './lib/components/ui/collapsible/index.js';
export * as Command from './lib/components/ui/command/index.js';
export * as Dialog from './lib/components/ui/dialog/index.js';
export * as DropdownMenu from './lib/components/ui/dropdown-menu/index.js';
export * as ContextMenu from './lib/components/ui/context-menu/index.js';
export * as Popover from './lib/components/ui/popover/index.js';
export * as Select from './lib/components/ui/select/index.js';
export * as Sheet from './lib/components/ui/sheet/index.js';
export * as Sidebar from './lib/components/ui/sidebar/index.js';
export * as Table from './lib/components/ui/table/index.js';
export * as Tabs from './lib/components/ui/tabs/index.js';
export * as Tooltip from './lib/components/ui/tooltip/index.js';
// Text that clips to its box and reveals itself on hover OR keyboard focus, gated on real overflow
// (see truncated-text/overflow.ts). The replacement for the `title` attribute on a capped cell.
export { TruncatedText, overflows } from './lib/components/ui/truncated-text/index.js';

// Template engine (shared by the platform + admin template editors).
export * from './lib/templates/types.js';
export {
	slugifyCode,
	parseTokens,
	pageSizePx,
	blankContent,
	makeBlock,
	makePage
} from './lib/templates/helpers.js';
export { default as TemplatePageView } from './lib/templates/template-page.svelte';
export { default as TemplateEditor } from './lib/templates/template-editor.svelte';
export { default as CreateTemplateDialog } from './lib/templates/create-template-dialog.svelte';

// Forms engine — shared shapes, the standard-field library, the dynamic-form
// renderer + its runtime (visibility/options/format/validate/masks), and the
// seed form fixtures + read helpers. Consumed by the platform builder/renderer
// and the client portal. The builder-only editors stay in the platform app.
export * from './lib/forms/types.js';
export * from './lib/forms/vocabularies.js';
export * from './lib/forms/standard-fields.js';
export { default as FormPicker } from './lib/forms/form-picker.svelte';
export { default as DynamicForm } from './lib/forms/dynamic-form.svelte';
export { default as FormSectionNav } from './lib/forms/form-section-nav.svelte';
export { wantsSectionNav, SECTION_NAV_MIN_FIELDS } from './lib/forms/section-nav.js';
export { forms, fieldsByForm, getForm, getFormFields, getFormByCode } from './lib/forms/mock.js';
// Static reference data (nationalities, …) for field option `source` refs — not mock form state.
export { nationalities, countries, formDataSources } from './lib/forms/reference.js';
// The MiFIR identifier rules — ONE implementation (`lib/national-identifier.ts`), for the reason
// its header gives: it existed twice, the form engine's copy and the client record's, and the two
// had diverged on the case that decides whether a CONCAT is derivable at all. The old names are
// kept as aliases so nothing outside this package had to move.
export {
	parseIdentifiers,
	pickIdentifierCountry,
	pickIdentifierCountry as pickNationalIdentifierCountryCode,
	pickIdentifierSpec,
	pickIdentifierSpec as pickNationalIdentifierSpec,
	pickAllIdentifierSpecs,
	pickAllIdentifierSpecs as pickAllNationalIdentifierSpecs,
	computeConcat,
	computeConcat as computeConcatIdentifier,
	nationalityOptions,
	type CountryIdentifierSpec,
	type IdentifierCountry,
	type IdentifierCountry as CountryRow
} from './lib/national-identifier.js';
export {
	formatFieldValue,
	formatDynamicFieldsForDisplay,
	type FormatFieldValueInput,
	type FormatDynamicFieldsForDisplayInput
} from './lib/forms/editor/format.js';
// The input masks, for DISPLAY as well as entry. A stored number is `+447715215412`; the same
// grouping the client typed it under is the one they should read it back in, and the client
// portal's onboarding review shows details it is asking a client to confirm. Exported rather
// than reimplemented so the two never drift into disagreeing about what a number looks like.
export {
	formatPhoneNumber,
	canonicalPhoneNumber,
	formatSortCode,
	canonicalSortCode,
	formatBankAccountNumber,
	phoneIssue,
	emailIssue
} from './lib/forms/editor/masks.js';
// THE phone and email entry fields, for every form, not only the dynamic one. Each refuses or names
// a bad value as it is typed; a bare `<Input type="tel">` waited for the API to refuse it on save.
export { default as PhoneNumberInput } from './lib/forms/field-inputs/phone-number-input.svelte';
export { default as EmailInput } from './lib/forms/field-inputs/email-input.svelte';

// Brand.
export { default as Logo } from './lib/components/brand/logo.svelte';
export { default as BrandingEditor } from './lib/components/branding/branding-editor.svelte';
// The PNG an email carries of an SVG logo/icon, drawn by the studio at upload (the API has no SVG
// rasteriser). Called by each app's upload action; see the module.
export { emailCopyFor } from './lib/components/branding/email-copy.js';
export {
	cardProductTypes,
	type CardProductType,
	type ShelfProduct
} from './lib/components/branding/card-products.js';
export { default as ClassificationCatalogue } from './lib/components/branding/classification-catalogue.svelte';
export type {
	ClientClassification,
	ClassificationDraft
} from './lib/components/branding/classifications.js';
export { default as GroupCatalogue } from './lib/components/branding/group-catalogue.svelte';
export type { ClientGroup, GroupDraft } from './lib/components/branding/groups.js';
export { default as ShellPreview } from './lib/components/branding/shell-preview.svelte';
export { default as LayoutBuilder } from './lib/components/branding/layout-builder.svelte';
// The Pages view's parts (Phase 3 of client-portal customisation). Consumed by BrandingEditor;
// exported for any future standalone page-management surface.
export { default as PagesRail } from './lib/components/branding/pages-rail.svelte';
export { default as BlockInspector } from './lib/components/branding/block-inspector.svelte';
export {
	CLIENT_BLOCKS,
	CLIENT_BLOCK_BY_TYPE,
	defaultClientLayout,
	blockW,
	blockH,
	minBlockW,
	clampBlockSpan,
	MAX_COLS,
	MAX_ROWS,
	type BlockType,
	type BlockMeta,
	type DataSource,
	type SettingDescriptor
} from './lib/components/branding/client-blocks.js';
// Portal pages + variant resolution — the page-level customisation contract (pins to
// branding/block-contract.json alongside app/branding.py; see registry.test.ts).
export {
	RESERVED_SLUGS,
	SHADOWABLE_SLUGS,
	PORTAL_SLUG_RE,
	PAGE_ICON_NAMES,
	PAGE_CAPS,
	PAGE_ICONS,
	slugify,
	mintBlockId,
	defaultPages,
	resolveAudienceLayout,
	derivePortalNav,
	type PortalNavItem
} from './lib/components/branding/portal-pages.js';
export {
	resolveVariant,
	VARIANT_THRESHOLDS,
	type VariantContext
} from './lib/components/branding/variants.js';
// Where a listing card's "View all" may point, decided from the firm's own page set — null when
// no other page shows that source, so the portal never draws a button to a page that 404s.
export {
	listingLinks,
	pagePath,
	LISTING_SOURCES,
	type ListingLinks,
	type ListingSource
} from './lib/components/branding/listing-links.js';
// The studio's pure page-state transitions: per-audience copy-on-write over a page's layouts
// (the Default | Individuals | Trusts & companies tabs) and the page CRUD the rail dispatches
// (mirrors the app/branding.py clamps, so the builder never produces a payload the API mangles).
export {
	layoutFor,
	isCustomised,
	customise,
	revert,
	setLayout,
	type Audience,
	type AudienceTab
} from './lib/components/branding/page-audience.js';
export {
	addPage,
	renamePage,
	setPageSlug,
	setPageIcon,
	setPageNav,
	removePage,
	reorderPages,
	duplicateBlock
} from './lib/components/branding/page-ops.js';
// Curated whole-portal page-set presets (built from defaultPages() transforms, contract-guarded;
// the studio's "Start from a preset…" picker replaces the working set with one).
export {
	PAGE_PRESETS,
	presetByKey,
	type PagePreset
} from './lib/components/branding/page-presets.js';
export {
	DEFAULT_CARD_DESIGNS,
	FINISHES,
	FINISH_LABELS,
	accountFace,
	designFor,
	finish,
	series
} from './lib/categorical.js';
export { provideCardDesigns, useCardDesigns } from './lib/card-designs.js';
// Theme generator lives in @graphene/auth (pure, owns the Branding contract); re-exported for convenience.
export {
	generateTheme,
	applyTheme,
	hexToOklch,
	colorToHex,
	THEME_STYLES,
	THEME_STYLE_KEYS,
	THEME_PRESETS,
	FONT_OPTIONS,
	type ThemeStyleKey,
	type ThemePreset,
	type GeneratedTheme
} from '@graphene/auth';
// The branding shape is the shared contract from `@graphene/auth`; re-exported here as
// `BrandingEditorValue` so consumers of the editor keep a stable name.
export type { Branding as BrandingEditorValue } from '@graphene/auth';

// Auth.
export { default as AuthCard } from './lib/components/auth/auth-card.svelte';
export { default as AuthField } from './lib/components/auth/auth-field.svelte';
export { default as AuthButton } from './lib/components/auth/auth-button.svelte';
export { default as LoginForm } from './lib/components/auth/login-form.svelte';
export { default as SetPasswordForm } from './lib/components/auth/set-password-form.svelte';
// Informative only — it reports, the identity provider decides. See the component's header.
export { default as PasswordStrength } from './lib/components/auth/password-strength.svelte';
export {
	loadScorer,
	scoreWith,
	toStrength,
	MIN_ACCEPTED_SCORE,
	SEGMENTS,
	type Strength,
	type StrengthTier
} from './lib/components/auth/password-strength.js';
export { default as ForgotPasswordForm } from './lib/components/auth/forgot-password-form.svelte';

// App shells.
export { default as AppShell } from './lib/components/shell/app-shell.svelte';
export { default as AuthShell } from './lib/components/shell/auth-shell.svelte';
export { default as SaaSShell } from './lib/components/shell/saas-shell.svelte';
export { default as ClientShell } from './lib/components/shell/client-shell.svelte';
export { default as NavProgress } from './lib/components/shell/nav-progress.svelte';
export { default as TenantSwitcher } from './lib/components/shell/tenant-switcher.svelte';
export { default as PageHeader } from './lib/components/shell/page-header.svelte';
export {
	CLIENT_HEADER_BUTTON_ACTIVE_CLASS,
	CLIENT_HEADER_BUTTON_CLASS,
	CLIENT_HEADER_ICON_CLASS
} from './lib/components/shell/client-chrome.js';
// Where "Back" goes, published by the app and read by PageHeader. See page-back.ts.
export {
	PAGE_BACK_KEY,
	getPageBack,
	setPageBack,
	type PageBackSource
} from './lib/components/shell/page-back.js';

// The roles & permissions editor (platform /team/roles + the admin tenant/model/operations
// pages — one implementation; the app injects its `enhance` and the pages own the actions).
export {
	default as RolesEditor,
	type RolesEditorPortal,
	type RolesEditorRole,
	type RolesEditorPermission
} from './lib/components/roles/roles-editor.svelte';
export {
	default as ActionsEditor,
	type ActionCategory,
	type ActionRow,
	type ActionRole
} from './lib/components/actions/actions-editor.svelte';
export {
	default as RunsListing,
	RUN_FILTER_KEYS
} from './lib/components/actions/runs-listing.svelte';
export {
	default as RunDetailView,
	type RunDetailActions
} from './lib/components/actions/run-detail.svelte';
export { default as RunPanel } from './lib/components/actions/run-panel.svelte';
export { default as RunNode } from './lib/components/actions/run-node.svelte';
export type {
	RunAccount,
	RunAttempt,
	RunDetailData,
	RunEvent,
	RunFailure,
	RunLinks,
	RunListRow,
	RunNodeData,
	RunPerson,
	RunState,
	RunStats,
	RunsPage
} from './lib/components/actions/run-types.js';
export { nextAction, type NextAction } from './lib/components/actions/next-action.js';
export { default as DecideDialogs } from './lib/components/actions/decide-dialogs.svelte';
export { reviewRowFromRun, reviewStatusOfRun } from './lib/components/actions/review-row.js';
export {
	runStatusLabel,
	runStatusTone,
	runInFlight,
	runSettled,
	runCanRetry,
	stepToneClass,
	attemptOutcomeLabel,
	attemptOutcomeTone,
	providerLabel,
	formatDuration,
	runDurationMs
} from './lib/components/actions/run-states.js';
export type {
	NavItem,
	NavGroup,
	NavContext,
	Tenant,
	Crumb,
	BackLink,
	Brand
} from './lib/components/shell/types.js';

// Charts (themed SVG, d3-shape/scale).
export { default as Sparkline } from './lib/components/charts/sparkline.svelte';
export {
	default as PerformanceChart,
	type PerformancePoint
} from './lib/components/charts/performance-chart.svelte';
export {
	default as AllocationDonut,
	type AllocationSlice
} from './lib/components/charts/allocation-donut.svelte';
// The performance chart's scrub geometry + accessible summary (pure, tested).
export { nearestIndex, chipFraction, chartAriaLabel } from './lib/components/charts/scrub.js';
// Allocation percentage labelling (largest-remainder, sums to 100) + negative-slice exclusion —
// shared by the donut and bars variants.
export {
	roundPercentages,
	allocationDisplay,
	type AllocationDisplay
} from './lib/components/charts/percentages.js';

// Money + investment (client portal).
export {
	formatGBP,
	formatSignedGBP,
	formatGBPCompact,
	formatMoney,
	formatSignedMoney,
	formatPct,
	formatPctPlain,
	gainDirection,
	gainTone,
	GAIN_TONES,
	minorDigits,
	PCT_DIGITS,
	sanePct,
	splitGBP,
	splitMoney
} from './lib/money';

// How long text behaves in a box that cannot grow (text-shell.ts). Exported because the apps' own
// listings and routes need the same ceilings the shared book components use — a cap that lives in
// two places is the drift `field-shell.ts` was written to stop.
export {
	TRUNCATE_ROW,
	TRUNCATE_BLOCK,
	WRAP_NAME,
	WRAP_TOKEN,
	LINK_RECORD,
	CELL_SPINE,
	CELL_NAME,
	CELL_ADVISER,
	CELL_MODEL,
	CELL_INSTRUMENT,
	CELL_TEXT,
	CELL_RAW
} from './lib/text-shell';

// How a CODE becomes words (sentence-case.ts). Root-exported for the same reason `text-shell` is:
// the apps print statuses, roles and kinds straight from the API on their own pages, and the two
// habits it replaces — rendering the raw key, or `class="capitalize"` — are what put a lowercase
// "draft" and a Title-Cased "Bank Transfer" in the same row of badges.
export { humanise, sentenceCase } from './lib/sentence-case';

export { formatBytes } from './lib/bytes';
export { isWebUrl } from './lib/urls';
export { countryName } from './lib/countries';
export { formatDate, formatDateTime, formatDayMonth } from './lib/dates';

// The CSV-injection guard both apps' exports wrap under their own names — see lib/csv.ts.
export { csvCell as escapeCsvCell, CSV_FORMULA_LEAD } from './lib/csv';

// Name-monogram initials, shared across every avatar-style tile — see lib/initials.ts.
export { initials, personInitials } from './lib/initials';

// Portal-search mechanics (each app's `$lib/search.ts` keeps only its own routing/permission
// policy over these — the ⌘K bars themselves stay app-side, they need `$app/navigation`).
export {
	SearchClient,
	searchUrl,
	settleSearch,
	scheduleSearch,
	routableSearchGroups,
	searchHrefFor,
	isSearchShortcut,
	SEARCH_MIN_QUERY_LENGTH,
	SEARCH_DEBOUNCE_MS,
	BOOK_SEARCH_SCOPES,
	BOOK_ROUTE_KEYS,
	type BookSearchScope,
	type BookRouteKey,
	type SearchHit,
	type SearchGroup,
	type SearchResults,
	type SearchRoutes,
	type SearchOutcome,
	type SearchQuerySink,
	relevanceOrderedSearchHits,
	searchCategoriesForGroups,
	searchHighlightParts,
	openSearchPalette,
	SEARCH_OPEN_EVENT,
	SPOTLIGHT_CHIP_CLASS,
	SPOTLIGHT_OVERLAY_CLASS,
	SPOTLIGHT_PANEL_CLASS,
	SPOTLIGHT_PANEL_STYLE,
	SPOTLIGHT_PHONE_CLOSE_CLASS,
	type SearchCategory,
	type SearchHighlightPart,
	type SearchOpenDetail
} from './lib/search.js';
// The portal's search palette — the platform's Spotlight bar, simplified: no quick actions,
// prefixes, guide or keycaps. Each app supplies its routes, categories and URL (its policy).
export { default as SpotlightSearch } from './lib/components/search/spotlight-search.svelte';
export { default as Stat } from './lib/components/data/stat.svelte';
export { default as StatTile } from './lib/components/data/stat-tile.svelte';
export {
	default as HeroPanel,
	type HeroMetric,
	type HeroDelta
} from './lib/components/data/hero-panel.svelte';
export { default as ClientAvatar } from './lib/components/data/client-avatar.svelte';
// The names the book's tables show, so an export can show the same ones.
export { clientName, providerReference } from './lib/components/book/clients/format.js';
export {
	birthLine,
	clientKind,
	completedAge,
	isChild,
	type ClientKind
} from './lib/components/data/client-kind.js';
export { default as PersonAvatar } from './lib/components/data/person-avatar.svelte';
// Advisers as stacked faces then names — the book's adviser column, whose `join(', ')` had no
// upper bound and set the table's width for every row.
export { default as AdviserStack } from './lib/components/data/adviser-stack.svelte';
export { default as PortfolioHeader } from './lib/components/investment/portfolio-header.svelte';
export { default as WrapperCard } from './lib/components/investment/wrapper-card.svelte';
export { default as AccountStack } from './lib/components/investment/account-stack.svelte';
export { default as HoldingsTable } from './lib/components/investment/holdings-table.svelte';
export { default as HoldingsGrouped } from './lib/components/investment/holdings-grouped.svelte';
// The one heading a client-portal widget wears (docs/design-language.md, Copy) — exported so the
// client app's own pages (the account record) wear it too.
export { default as WidgetHeader } from './lib/components/investment/widget-header.svelte';
// The placeholder logo disc beside a holding, and its two pure derivations — until real instrument
// logos are wired, at which point the component's inside changes and no caller does.
export { default as InstrumentIcon } from './lib/components/investment/instrument-icon.svelte';
export {
	INSTRUMENT_TONES,
	INSTRUMENT_TONE_CLASSES,
	instrumentInitials,
	instrumentTone,
	isCashHolding,
	type InstrumentTone
} from './lib/components/investment/instrument-icon.js';
export { default as ActivityList } from './lib/components/investment/activity-list.svelte';
export { default as AdviserCard } from './lib/components/investment/adviser-card.svelte';
export { default as NewsFeed } from './lib/components/investment/news-feed.svelte';
export { default as NewsLibrary } from './lib/components/investment/news-library.svelte';
export { default as NewsMagazine } from './lib/components/investment/news-magazine.svelte';
export { default as NewsJournal } from './lib/components/investment/news-journal.svelte';
export { default as InsightCover } from './lib/components/investment/insight-cover.svelte';
export {
	KIND_PLURAL,
	KIND_SINGULAR,
	kindOf,
	readingTime,
	relatedInsights,
	stampOf,
	topicSlots,
	type NewsKind
} from './lib/components/investment/insights.js';
export { default as DocumentsTimeline } from './lib/components/investment/documents-timeline.svelte';
export { default as DocumentsList } from './lib/components/investment/documents-list.svelte';
export { default as ClientHome } from './lib/components/investment/client-home.svelte';
// The generalised portal-page renderer (Phase 2 of client-portal customisation) + the block
// components that arrived with it. Demo gating is resolveSlices' one decision — see slices.ts.
export { default as PortalPage } from './lib/components/investment/portal-page.svelte';
export { default as ActionItemsCard } from './lib/components/investment/action-items-card.svelte';
export { default as OnboardingProgress } from './lib/components/investment/onboarding-progress.svelte';
// The bare milestone stepper, exported standalone for the client app's own /onboarding page
// (which IS the destination the block's link points at, so it needs the stepper without the
// clickable-card chrome onboarding-milestones.svelte wraps it in for the home-page block).
export { default as OnboardingStages } from './lib/components/investment/onboarding-stages.svelte';
// The /onboarding hero: the client's next action, or an honest account of who the application is
// waiting on. Presentation only — the caller owns what starting a step does.
export { default as OnboardingNextAction } from './lib/components/investment/onboarding-next-action.svelte';
export { default as MessageCard } from './lib/components/investment/message-card.svelte';
// The block variant components (Phase 4 of client-portal customisation) — PortalPage dispatches
// to these from the concrete settings.variant; exported for standalone reuse.
export { default as PortfolioHeaderCompact } from './lib/components/investment/portfolio-header-compact.svelte';
// The two statement looks (explicit variants — auto never picks them) + the pieces the three
// big header looks share: period logic, provenance stamps, the count-up figure.
export { default as PortfolioHeaderCanvas } from './lib/components/investment/portfolio-header-canvas.svelte';
export { default as PortfolioHeaderLedger } from './lib/components/investment/portfolio-header-ledger.svelte';
export { default as AnimatedMoney } from './lib/components/investment/animated-money.svelte';
export {
	default as HeaderActions,
	normaliseActions,
	shownMoneyButtons,
	type HeaderActionsMode
} from './lib/components/investment/header-actions.svelte';
export {
	periodKeys,
	slicePeriod,
	changeDirection,
	normalisePeriod,
	PERIOD_LABELS,
	type PeriodKey,
	type ChangeDirection
} from './lib/components/investment/periods.js';
export { asAt, britishDate, periodBasis } from './lib/components/investment/provenance.js';
export { countUpAt, COUNT_UP_MS } from './lib/components/investment/count-up.js';
export { default as AccountCards } from './lib/components/investment/account-cards.svelte';
export { default as AccountsTable } from './lib/components/investment/accounts-table.svelte';
export { default as AccountsSummary } from './lib/components/investment/accounts-summary.svelte';
export { default as HoldingsFullTable } from './lib/components/investment/holdings-full-table.svelte';
export { default as LookThroughButton } from './lib/components/investment/look-through-button.svelte';
export { default as ActivityTable } from './lib/components/investment/activity-table.svelte';
export { default as AllocationBars } from './lib/components/investment/allocation-bars.svelte';
// The per-widget LOOKS system: resolveLook maps a block's `look` setting + the portal finish to
// a concrete design (looks.ts, table-tested), and PortalPage dispatches to these — the
// chrome-less looks render on the page ground with no Card.Root, each owning its whole
// presentation (title treatment included).
export { resolveLook, type Finish } from './lib/components/investment/looks.js';
export { default as AccountsFloat } from './lib/components/investment/accounts-float.svelte';
export { default as AccountsInk } from './lib/components/investment/accounts-ink.svelte';
export { default as AllocationOrbit } from './lib/components/investment/allocation-orbit.svelte';
export { default as AllocationSpectrum } from './lib/components/investment/allocation-spectrum.svelte';
export { default as HoldingsSheet } from './lib/components/investment/holdings-sheet.svelte';
export { default as HoldingsValueBars } from './lib/components/investment/holdings-value-bars.svelte';
export { default as ActivityTimeline } from './lib/components/investment/activity-timeline.svelte';
export { default as DocumentsShelf } from './lib/components/investment/documents-shelf.svelte';
export { default as DocumentsIndex } from './lib/components/investment/documents-index.svelte';
export { default as AdviserPortrait } from './lib/components/investment/adviser-portrait.svelte';
export { default as AdviserStrip } from './lib/components/investment/adviser-strip.svelte';
export { default as ActionItemsBanner } from './lib/components/investment/action-items-banner.svelte';
export { default as MessageNote } from './lib/components/investment/message-note.svelte';
export { default as MessageBanner } from './lib/components/investment/message-banner.svelte';
export { default as OnboardingSlim } from './lib/components/investment/onboarding-slim.svelte';
export { default as OnboardingMilestones } from './lib/components/investment/onboarding-milestones.svelte';
export {
	resolveSlices,
	awaitingValuation,
	clampLimit,
	clampText,
	deriveWrapperGroups,
	tablePagination,
	type Slices,
	type TablePaginationState
} from './lib/components/investment/slices.js';
// Account grouping (the accounts block's group_by setting) — pure, shared by the cards/stack/
// summary variants.
export {
	groupWrappers,
	groupLabel,
	isBeneficiary,
	normaliseGroupBy,
	type GroupAxis,
	type WrapperSection
} from './lib/components/investment/grouping.js';
// The listing toolbars (the blocks' `toolbar` setting — the legacy listing pages' search/filter
// affordances): the shared control row, plus the pure matching/option/unread rules behind it.
export { default as WidgetToolbar } from './lib/components/investment/widget-toolbar.svelte';
// The portal's one browse bar — tabs + pull-down + search-as-a-button — behind the Insights
// library and the Docs page's `tabs` category style.
export { default as BrowseBar } from './lib/components/investment/browse-bar.svelte';
export {
	accountItems,
	categoryCounts,
	filterListing,
	matchesText,
	unreadNote,
	type CategoryPill,
	type ListingQuery
} from './lib/components/investment/listing.js';
// A movement kind → words, shared so every activity look and the client app defer to ONE
// vocabulary — and to the SAME words the adviser platform uses (see activity.ts).
export {
	ACTIVITY_LABELS,
	activityLabel,
	activityUnits
} from './lib/components/investment/activity.js';
// Account status → tone, shared so the widgets and the client app defer to ONE mapping.
export {
	accountStatusChip,
	accountStatusTone
} from './lib/components/investment/account-status.js';

// Shared demo fixtures (client portal) — placeholder content + the branding preview's data.
export { clientDemo } from './lib/fixtures/demo.js';
// The studio's preview clients — three deterministic personas either side of the variant
// thresholds (they feed PortalPage and the auto-variant readout).
export { PERSONAS, personaByKey, type Persona } from './lib/fixtures/personas.js';
export type {
	Wrapper,
	WrapperType,
	WrapperGroup,
	Holding,
	Activity,
	ActivityType,
	Adviser,
	NewsItem,
	ClientDocument,
	DocumentCategory,
	DocumentFormat,
	ClientHomeData,
	PortalActionItem,
	PortalOnboarding,
	PortalOnboardingStage,
	OnboardingNextStep,
	PortalPageData
} from './lib/components/investment/types.js';

export { reportUnhandled } from './lib/report-unhandled.js';

// The action-event stream reader — the master-detail EventStream (admin Events page, cross-firm +
// per-tenant; the platform Events page for a firm's own slice) + the pure describe/timestamp
// helpers behind it.
export {
	EventStream,
	EventFeed,
	groupEvents,
	actorSuggestions,
	type ActorLike,
	EMPTY_ACTION_LOG_FILTERS,
	// The one filter↔API-query mapping, shared by both portals' Events loaders and "Load more"
	// proxies (exclusive `until`, tri-state `proxied` — see `audit/action-log-query.ts`).
	actionLogFiltersFromSearchParams,
	actionLogBrowserQuery,
	actionLogQuery,
	actionLogUntilBound,
	type ActionLogPage,
	describeActionEvent,
	actorLabel,
	entityKindLabel,
	methodLabel,
	methodRoute,
	statusLabel,
	statusTone,
	timestampParts,
	highlightJson,
	previewValue,
	shortenForJson,
	type ActionLogFilters,
	type ActionLogTenant,
	type ActionEntity,
	type ActionEvent,
	type GroupedActionEvent,
	type HighlightSeg,
	type TimestampParts
} from './lib/components/audit/index.js';

// In-app notifications (notifications.*, api 0063): the shell bell + badge, the full-page feed, the
// staff channel-preferences surface, and the pure describe helpers behind them.
export {
	NotificationBell,
	StreamedNotificationBell,
	NotificationFeed,
	NotificationItem,
	NotificationToast,
	NotificationPreferences,
	ClientNotificationPreferences,
	NotificationRules,
	categoryMeta,
	toneClasses,
	relativeTime,
	type NotificationItemData,
	type NotificationPreference,
	type NotificationTone,
	type CategoryMeta,
	type CatalogueTrigger,
	type CatalogueAudience,
	type NotificationRule,
	type RegistryEvent,
	type RuleDraft,
	createPollController,
	pollWhileVisible,
	REFRESH_WHILE,
	type PollController,
	type EmailTemplateOption,
	WRAPPER_CODE
} from './lib/components/notifications/index.js';

// Asks for a page again while a wait is still in flight, and only while the tab is visible.
export { default as RefreshWhile } from './lib/components/ui/refresh-while/refresh-while.svelte';

// The shared book surfaces (the portal waterfall: platform + admin render the same pages).
export * from './lib/components/book/clients/index.js';
export * from './lib/components/book/accounts/index.js';

// Reaching the bottom of a paged list loads the next page (`use:whenVisible`). Exported because
// every paged surface in the estate has the same "Load more" button and the same reason to stop
// asking for a press — the audit trail is simply the first to use it.
export { whenVisible, type WhenVisibleOptions } from './lib/hooks/when-visible.js';
