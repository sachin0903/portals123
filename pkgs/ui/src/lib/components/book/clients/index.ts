// The shared client-book surfaces (clients listing + client record) consumed by BOTH the
// platform portal and the admin portal (the "waterfall": admin sees everything a tenant sees).
//
// Contract: data arrives as typed props from the app's own loaders, every href comes from the
// injected `links`, and mutations only exist when the app passes `actions` (its form-action
// names + its own `enhance` from `$app/forms` — pkgs/ui cannot import SvelteKit virtuals).
// Actions absent ⇒ the surfaces render read-only, which is the admin wave's first mode.

export { default as ClientsListing } from './clients-listing.svelte';
export type { ClientsListingActions } from './clients-listing.svelte';
export { default as ClientRecord } from './client-record.svelte';
export type { ClientRecordActions } from './client-record.svelte';
export { default as ClientJourneys } from './client-journeys.svelte';
export { default as ClientJourney } from './client-journey.svelte';
export type {
	ClientJourneySummary,
	ClientJourneyDetail,
	ClientJourneyForm,
	ClientJourneyDocument,
	ClientJourneyOther
} from './client-journey.js';

// The data shapes the components read (structural — an app's richer API types are assignable).
export type {
	ClientType,
	ServiceType,
	ClientAddress,
	ClientContact,
	ClientListRow,
	BankDetails,
	ClientAccountRow,
	InstructionKind,
	InstructionRow,
	ClientNoteRow,
	ClientAdviserRow,
	ClientRecordDetail,
	ClientRecordView,
	CatalogModel,
	ClientTaskItem,
	ClientsListingLinks,
	ClientRecordLinks,
	VulnerabilityReason,
	VulnerabilityCharacteristic,
	TaxRelief
} from './types.js';

// The FCA's own driver labels (FG21/1), exported so a portal's own copy cannot drift from the
// badge row -- and so a test asserts the same words the adviser reads.
export {
	VULNERABILITY_LABELS,
	// The 24 characteristics beneath those drivers (0116) and their FG21/1 grouping, exported for
	// the same reason as the driver labels: a portal's own copy would drift from what the adviser
	// reads, and the API container ships without pkgs/ui so only a test holds the two together.
	VULNERABILITY_CHARACTERISTIC_LABELS,
	CHARACTERISTICS_BY_DRIVER,
	CHARACTERISTIC_SELECT_OPTIONS,
	TAX_RELIEF_LABELS,
	VULNERABLE_CARD_CLASS,
	durationChoice,
	durationValue,
	isFlaggedVulnerable,
	toggleMember,
	toggleCharacteristic,
	toggleReason
} from './types.js';
// A client's team (`crm.client_advisers`): the four roles and their words, once. The platform's
// form action validates against the same list the Team dialog offers, and every surface that
// names a team member's role (the rail, the AML header, the automation builder) reads it here.
export {
	CLIENT_TEAM_ROLES,
	CLIENT_TEAM_ROLE_LABELS,
	CLIENT_TEAM_ROLE_ITEMS,
	DEFAULT_CLIENT_TEAM_ROLE,
	isClientTeamRole,
	clientTeamRoleLabel,
	readClientTeamRole,
	orderClientTeam,
	clientAdviser,
	canHoldClientTeamRole,
	clientTeamPickerItems,
	replacedAdviserNote,
	type ClientTeamRole
} from './team.js';
// The disclosure strip itself — rendered by the client record AND the account record, so an
// adviser sees the same statement wherever they meet the person.
export { default as VulnerabilityBanner } from './vulnerability-banner.svelte';

// Injected-enhance contract + the shared mutation feedback path.
export {
	withToast,
	errorOf,
	type Enhance,
	type EnhanceSubmit,
	type EnhanceCallback,
	type EnhanceOutcome,
	type EnhanceUpdate
} from './form-enhance.js';
export { setEnabledMessage } from './messages.js';

// Pure display/mapping helpers the apps' loaders and pages share.
export { clientName, clientSubline, providerReference, shortLocation } from './format.js';
export {
	applyHoldings,
	contactHoldingsQuery,
	unmeasuredIds,
	type ContactHolding,
	type ContactHoldings
} from './holdings.js';
export { default as AccountTags } from './account-tags.svelte';
export { canCloseClient, clientStatus, type ClientStatus } from './status.js';
export {
	standingRows,
	scheduleLabel,
	ordinal,
	autoInvestLabel,
	type StandingAccount,
	type StandingMovement,
	type StandingRow
} from './standing.js';
// What KIND of client — one vocabulary for the listing's column, its filter and the record's
// Details panel, and for the export, which has to print the same word the screen shows.
export { clientTypeLabel, ORGANISATION_SUB_TYPES } from './client-type.js';
export {
	bookSummary,
	inBookSegment,
	matchesClientQuery,
	visibleClientRows,
	BOOK_SEGMENTS,
	BOOK_SORTS,
	type BookListRow,
	type BookSegment,
	type BookSort
} from './book-filters.js';
export {
	screeningRows,
	isFullyScreened,
	type ScreeningKind,
	type ScreeningStatus,
	type ScreeningCheck,
	type ScreeningRow
} from './screening.js';
export { personComplianceFacts, takesFatcaDocument, type PersonFact } from './person-facts.js';
export {
	allocationSegments,
	ALLOC_PALETTE,
	type Allocation,
	type AllocationSegment
} from './allocation.js';
export {
	validateBank,
	bankDirectoryError,
	readBankDetails,
	formatIban,
	bankSchemeOf,
	bankDisplayRows,
	bankMethodLabel,
	BANK_SCHEME_OPTIONS,
	BANK_METHOD_OPTIONS,
	CLEARED_BANK,
	type BankInput,
	type BankValidation,
	type BankScheme,
	type BankMethod,
	type BankDirectoryStatus
} from './bank.js';
export {
	asNumber,
	classifyHolding,
	activityType,
	signedAmount,
	toActivity,
	clientBookTotals,
	applyHoldingsSummary,
	type RawHolding,
	type HoldingsSummary,
	type HoldingsSummaryView,
	type RawTxn,
	type ActivityRow
} from './lake.js';
export {
	basisByAccount,
	basisFromSummaryLegs,
	committedByAccount,
	accountFunding,
	INSTRUCTION_LEG,
	type FundingBasis,
	type Committed,
	type AccountFunding
} from './funding.js';
