<script module lang="ts">
	import type { FilterDef } from '../listing/filter-panel.svelte';

	/** A pure function so it can be pinned in a test without rendering the panel, which lives
	 *  behind a popover shut at SSR. Keys match the /accounts book's for the same two facets. */
	export function accountFilterDefs(input: {
		statusOptions: FilterDef['options'];
		typeOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{ key: 'status', label: 'Status', options: input.statusOptions },
			{ key: 'type', label: 'Product type', options: input.typeOptions }
		];
	}
</script>

<script lang="ts">
	// The client record — the platform portal's /clients/[uid] page (Overview) and
	// /clients/[uid]/details, extracted so the admin portal can render the same record (the
	// portal waterfall). Overview states accounts, a read-only copy of personal details and notes;
	// Details states the person cards. The identity header carries email, mobile and address in a
	// row, plus a portal-access badge.
	//
	// Data comes in as typed props (the app's loaders fetch it), every href is built by the injected
	// `links`, and EVERY mutation exists only when the app passes `actions` (SvelteKit form-action
	// names + its own `enhance`) — absent, the record renders read-only: details/bank as
	// definition lists, no money buttons, no note form, no mandate control, no cancel affordance.
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import FileChartLineIcon from '@lucide/svelte/icons/file-chart-line';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import XIcon from '@lucide/svelte/icons/x';
	import { tick, untrack, type Snippet } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { cn } from '../../../utils.js';
	import PendingReview from '../reviews/pending-review.svelte';
	import ReviewChange from '../reviews/review-change.svelte';
	import ReviewHoldBanner from '../reviews/review-hold-banner.svelte';
	import {
		reviewCaseRef,
		reviewsForCard,
		reviewsOffTheCards,
		type DetailCard
	} from '../reviews/reviews.js';
	import type { ReviewRow } from '../reviews/types.js';
	import { Badge } from '../../ui/badge/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import { CELL_MODEL, CELL_SPINE, LINK_RECORD, WRAP_NAME } from '../../../text-shell.js';
	import { Button } from '../../ui/button/index.js';
	import { CopyButton } from '../../ui/copy-button/index.js';
	import { Card, CardAction, CardContent, CardHeader, CardTitle } from '../../ui/card/index.js';
	import { Checkbox } from '../../ui/checkbox/index.js';
	import { formatPhoneNumber } from '../../../forms/editor/masks.js';
	import EmailInput from '../../../forms/field-inputs/email-input.svelte';
	import PhoneNumberInput from '../../../forms/field-inputs/phone-number-input.svelte';
	import AdviserStack from '../../data/adviser-stack.svelte';
	import * as Collapsible from '../../ui/collapsible/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import RouteIcon from '@lucide/svelte/icons/route';
	import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import ArchiveIcon from '@lucide/svelte/icons/archive';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import {
		DataTable,
		type DataTableColumn,
		type DataTableSort
	} from '../../ui/data-table/index.js';
	import * as Dialog from '../../ui/dialog/index.js';
	import * as Sheet from '../../ui/sheet/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import { Money } from '../../ui/money/index.js';
	import MoneyInput from '../../ui/money-input/money-input.svelte';
	import {
		OCCUPATION_OPTIONS,
		titleItems,
		SOURCE_OF_FUNDS_OPTIONS,
		SOURCE_OF_WEALTH_OPTIONS
	} from './types.js';
	import {
		EMPLOYMENT_OPTIONS,
		GENDER_OPTIONS,
		MARITAL_OPTIONS,
		withStoredChoice
	} from '../../../forms/vocabularies.js';
	import {
		computeConcat,
		nationalityOptions,
		pickIdentifierCountry,
		pickIdentifierSpec
	} from '../../../national-identifier.js';
	import {
		isNinoType,
		nationalInsuranceError,
		NATIONAL_INSURANCE_MESSAGE,
		NATIONAL_INSURANCE_PATTERN
	} from '../../../forms/national-insurance.js';
	import type { BankCheckResult, BankCheckStatus } from '../../ui/bank-account-check/index.js';
	import BankMark from '../../../forms/field-inputs/bank-mark.svelte';
	import { AddressLookup, type Address } from '../../ui/address-lookup/index.js';
	import { Combobox } from '../../ui/combobox/index.js';
	import { Separator } from '../../ui/separator/index.js';
	import { MultiSelect } from '../../ui/multi-select/index.js';
	import MailIcon from '@lucide/svelte/icons/mail';
	import CheckIcon from '@lucide/svelte/icons/check';
	import NoteComposer from '../notes/composer.svelte';
	import CircleDashedIcon from '@lucide/svelte/icons/circle-dashed';
	import PhoneIcon from '@lucide/svelte/icons/phone';
	import SmartphoneIcon from '@lucide/svelte/icons/smartphone';
	import MapPinIcon from '@lucide/svelte/icons/map-pin';
	import HashIcon from '@lucide/svelte/icons/hash';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import { default as Pagination } from '../../ui/pagination/pagination.svelte';
	import { rangeLabelFromOffset } from '../listing/query.js';
	import ListingToolbar from '../listing/listing-toolbar.svelte';
	import { humanise, matches, optionsFrom, search } from '../listing/filters.js';
	// The account surfaces' vocabulary, imported rather than restated: this table shows the same
	// accounts the accounts listing does, and one of them saying "Advisory" where the other says
	// "—" about the same row is exactly the drift `serviceTypeLabel` exists to prevent. Safe from
	// a cycle — `service-type.ts` imports nothing.
	import { serviceTypeLabel } from '../accounts/service-type.js';
	import { clientTypeLabel, ORGANISATION_SUB_TYPES } from './client-type.js';
	import { FATCA_DOCUMENT_FACT, fatcaFormLabel, personComplianceFacts } from './person-facts.js';
	import FatcaDocumentField from './fatca-document-field.svelte';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { formatDate } from '../../../dates.js';
	import { statusTone } from '../listing/status-tone.js';
	import { default as Segmented } from '../../ui/segmented/segmented.svelte';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { Switch } from '../../ui/switch/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import { default as PersonAvatar } from '../../data/person-avatar.svelte';
	import { default as Stat } from '../../data/stat.svelte';
	import { formatGBP, formatMoney, formatSignedMoney } from '../../../money.js';
	import { autoInvestLabel, scheduleLabel, standingRows, type StandingRow } from './standing.js';
	// One definition of "is this a pension", shared with the accounts surfaces rather than a second
	// list of wrapper strings here — SIPP, PENSION and SSAS, case-insensitively.
	import {
		clientBorneRailFee,
		railFeeForMethod,
		ukWithdrawalMethod,
		WITHDRAWAL_METHOD_LABEL,
		withdrawalDestination,
		withdrawalTotalDebit,
		type WithdrawalPaymentMethod
	} from '../accounts/instruction.js';
	import { allocationSegments, type Allocation } from './allocation.js';
	import AllocationDonut from '../../data/allocation-donut.svelte';
	import RecordHero from '../../data/record-hero.svelte';
	import { clientKind } from '../../data/client-kind.js';
	import BankAccountFields from './bank-fields.svelte';
	import BankAccountNumberInput from '../../../forms/field-inputs/bank-account-number-input.svelte';
	import SortCodeInput from '../../../forms/field-inputs/sort-code-input.svelte';
	import { BankAccountCheck, bankDigits } from '../../ui/bank-account-check/index.js';
	import {
		BANK_METHOD_OPTIONS,
		bankDirectoryError,
		bankDisplayRows,
		bankSchemeOf,
		readBankDetails,
		validateBank,
		type BankInput
	} from './bank.js';
	import { withToast, type Enhance, type EnhanceSubmit } from './form-enhance.js';
	import { clientName, providerReference } from './format.js';
	import type { AccountFunding } from './funding.js';
	import { canCloseClient, clientStatus } from './status.js';
	import type { ScreeningCheck } from './screening.js';
	import {
		CAPACITY_LABELS,
		CHARACTERISTIC_SELECT_OPTIONS,
		durationChoice,
		TAX_RELIEF_LABELS,
		toggleReason,
		VULNERABILITY_CHARACTERISTIC_LABELS,
		VULNERABILITY_LABELS,
		VULNERABLE_CARD_CLASS
	} from './types.js';
	import { default as VulnerabilityBanner } from './vulnerability-banner.svelte';
	import ClientActiveJourneys from './client-active-journeys.svelte';
	import type { ClientJourneySummary } from './client-journey.js';
	import RecentInstructions from './recent-instructions.svelte';
	import {
		CLIENT_TEAM_ROLE_ITEMS,
		DEFAULT_CLIENT_TEAM_ROLE,
		canHoldClientTeamRole,
		clientTeamPickerItems,
		clientTeamRoleLabel,
		isClientTeamRole,
		orderClientTeam,
		replacedAdviserNote,
		type ClientTeamRole
	} from './team.js';
	import type {
		BankDetails,
		ClientAccountRow,
		ClientNoteRow,
		ClientRecordDetail,
		ClientRecordLinks,
		CountryOption,
		ClientRecordView,
		ClientTaskItem,
		FirmMemberOption,
		InstructionKind,
		InstructionRow,
		TaxRelief,
		VulnerabilityReason
	} from './types.js';

	/** The mutations the record can host — the app's form-action names plus its own `enhance`.
	 *  Absent ⇒ the whole record is read-only. Every action name is OPTIONAL because the two
	 *  permission families gate different affordances (`contacts:write` edits the person,
	 *  `instructions:write` moves their money — see docs/api.md "six splits"): the app passes
	 *  exactly the names the caller's permissions earn, and each affordance renders only when
	 *  its action name is present. */
	export type ClientRecordActions = {
		/** SvelteKit's `enhance`, injected because pkgs/ui cannot import `$app/forms`. */
		enhance: Enhance;
		// ── contacts:write — the client themselves ─────────────────────────────
		updateClient?: string;
		/** Vulnerability has its own action, not a field on `updateClient`: the API gives it its own
		 *  validated door (special-category-adjacent data, its own audit row), and a details-form
		 *  validation failure must not be able to half-save it. */
		setVulnerability?: string;
		/** Close a client who holds no accounts, or whose every account is worth zero. Replaces
		 *  Archive. The API refuses a valued book (409); the item is hidden here for the same rule. */
		closeClient?: string;
		/** Reopen a closed client. Shown on the record itself now — Close no longer 404s the page. */
		restoreClient?: string;
		/** Invite this client to the portal (create-or-reuse login + link + set-password email).
		 *  Hidden when they already have portal access. Needs contacts:write AND a members/teams/
		 *  profiles write — the API is the union of those two primitives. */
		invitePortal?: string;
		/** Pin this client to a book (or clear the pin so rules rematch). */
		pinClassification?: string;
		/** Replace the groups this client wears. */
		setGroups?: string;
		addNote?: string;
		/** Delete a note. The API restricts it to the caller's OWN notes, so the affordance shows on
		 *  every row and the refusal is the server's — the component has no way to know who wrote
		 *  one (`ClientNoteRow` carries an author NAME, not an id, and matching on a display name is
		 *  the inference this estate refuses). Absent ⇒ no cross. */
		deleteNote?: string;
		// ── instructions:write — money movement ────────────────────────────────
		// Bank details and the model mandate travel with the money family: they are what a
		// withdrawal pays to and what makes the model actions apply, not client identity.
		updateBankAccount?: string;
		setAccountModel?: string;
		createInstruction?: string;
		cancelInstruction?: string;
		/** Cancel a pending four-eye request the caller submitted. */
		cancelReview?: string;
		// ── contacts:write — who is attached to the client ─────────────────────
		// The two popouts behind Team's menu and the key beside the name. They were a People PAGE
		// (`links.people`, now gone): adding someone to the team or linking a login is a one-field
		// errand off the record, and each is its own endpoint, so each is its own action.
		// `assignAdviser` posts `adviser_id` and `role` (one of `CLIENT_TEAM_ROLES`).
		assignAdviser?: string;
		unassignAdviser?: string;
		linkUser?: string;
		unlinkUser?: string;
		/** Open the illustration form in place (the platform shows a drawer). When absent the
		 *  button falls back to `links.illustrations`, so a read-only mount still links out. */
		onillustrate?: () => void;
		/** Open this client's portal, read-only, as them (docs/identity-and-access.md, "View as").
		 *  The platform asks for a reason first and opens the new tab; present only for a reader
		 *  who already reaches this client (`contacts:read`). */
		onviewas?: () => void;
	};

	let {
		detail,
		allocations = [],
		/** Cash across the client's holdings, scaled to AUM alongside the allocation ring. */
		cash = 0,
		/**
		 * What the client PAID for what they hold, and the difference between that and today's value.
		 * Both are `null` until a loader supplies them, and both render as an em dash when they are —
		 * deliberately, because a cost basis is the one figure on this card you cannot infer. Summing
		 * account values gives AUM; there is nothing on the record to derive a cost from, and a zero
		 * here would read as "bought for nothing", i.e. all gain.
		 *
		 * The lake has both per position (`book_cost`, `unrealised_gain` on the conformed layer), so
		 * this is a loader change rather than new plumbing — see docs/platform-model.md §3.
		 */
		bookCost = null,
		unrealisedGain = null,
		holdingsError = false,
		instructions = [],
		/** Per-account fundable money (see ./funding.ts) — what the money dialogs state. */
		funding = [],
		bankAccount = null,
		/** Open four-eye requests on this client — live values stay current; this is the proposal. */
		pendingReviews = [],
		/** Catalogue keys whose save asks for a reason. Approval can be on without one. */
		justificationRequired = [],
		/** Open action items, presentation-ready (the app applies its own task/due rules). */
		tasks = [],
		/** The wrapper the illustration button names — "SIPP illustration" rather than a generic word,
		 *  supplied by the app from the pension the client actually holds. It falls back to "Pension"
		 *  because an illustration is what a client is shown BEFORE they hold anything, so a prospect
		 *  with no accounts still has one to run. */
		pensionLabel = 'Pension',
		countries: countriesProp = [],
		/** The firm's identities, for the Advisers / Profiles popouts' pickers (`GET /firm/members`).
		 *  Empty is a real state — a firm with nobody else to assign — and the popout says so. */
		members: membersProp = [],
		/** Their read failed. The popout keeps its lists and says the pickers may be incomplete,
		 *  rather than letting an empty picker read as "your firm has nobody to assign". */
		membersError: membersErrorProp = false,
		/** Same-origin picker URLs. When set, members and countries load when the popout / combobox
		 *  opens rather than on first paint. */
		pickers,
		/** Which band to render. Defaults to `all` so a mount with no routes (the admin book)
		 *  still stacks the whole record. */
		section = 'all',
		links,
		classifications = [],
		resolvedClassification = null,
		groups = [],
		assignedGroups = [],
		actions = null,
		/** The app's work queue (e.g. the platform's Action required panel), rendered under the
		 *  header. Injected because categorisation is app behaviour, not record display. */
		queue,
		/** Journeys this client is on. The overview names the ones still open. */
		journeys = [],
		/** Re-runs the app's load (`invalidateAll`) behind the error banners' Retry. */
		onretry
	}: {
		detail: ClientRecordDetail;
		screeningChecks?: ScreeningCheck[];
		allocations?: Allocation[];
		cash?: number;
		bookCost?: number | null;
		unrealisedGain?: number | null;
		holdingsError?: boolean;
		instructions?: InstructionRow[];
		funding?: AccountFunding[];
		bankAccount?: BankDetails | null;
		pendingReviews?: ReviewRow[];
		justificationRequired?: string[];
		tasks?: ClientTaskItem[];
		pensionLabel?: string;
		/** `ref.countries` (0050) — the ONE country vocabulary, injected by the loader. Nationality,
		 *  tax residency and country of birth all STORE ISO codes and were free-text boxes until
		 *  Sept 2026, so a firm typed "United Kingdom" into a column holding `GB`. Empty ⇒ those
		 *  fields fall back to a plain code box, which is worse than a picker and much better than a
		 *  record that will not render. */
		countries?: CountryOption[];
		members?: FirmMemberOption[];
		membersError?: boolean;
		pickers?: { members?: string; countries?: string };
		section?: ClientRecordView;
		links: ClientRecordLinks;
		/** Catalogue of client books — empty unless the loader asked. */
		classifications?: { uid: string; name: string; slug: string }[];
		/** The resolved book for this client (pin, match, or default). */
		resolvedClassification?: {
			uid: string;
			name: string;
			slug: string;
			source: 'pinned' | 'matched' | 'default';
		} | null;
		/** The firm's groups — empty unless the loader asked. */
		groups?: { uid: string; name: string; slug: string }[];
		/** The groups this client wears. */
		assignedGroups?: { uid: string; name: string; slug: string }[];
		actions?: ClientRecordActions | null;
		queue?: Snippet;
		journeys?: readonly ClientJourneySummary[];
		onretry?: () => void;
	} = $props();

	let lazyMembers = $state<FirmMemberOption[] | null>(null);
	let lazyCountries = $state<CountryOption[] | null>(null);
	let lazyMembersError = $state(false);
	const members = $derived(lazyMembers ?? membersProp);
	const countries = $derived(lazyCountries ?? countriesProp);
	const membersError = $derived(lazyMembers !== null ? lazyMembersError : membersErrorProp);

	async function loadPickerJson<T>(url: string): Promise<T | null> {
		try {
			const res = await fetch(url);
			if (!res.ok) return null;
			return (await res.json()) as T;
		} catch {
			return null;
		}
	}

	let membersInflight = false;
	async function ensureMembers() {
		if (!pickers?.members || lazyMembers !== null || membersInflight) return;
		membersInflight = true;
		const rows = await loadPickerJson<FirmMemberOption[]>(pickers.members);
		if (rows) lazyMembers = rows;
		else {
			lazyMembers = [];
			lazyMembersError = true;
		}
		membersInflight = false;
	}

	let countriesInflight = false;
	async function ensureCountries() {
		if (!pickers?.countries || lazyCountries !== null || countriesInflight) return;
		countriesInflight = true;
		const rows = await loadPickerJson<CountryOption[]>(pickers.countries);
		lazyCountries = rows ?? [];
		countriesInflight = false;
	}

	// `use:` needs a plain identifier: alias the injected enhance (never invoked when read-only —
	// every form is inside an `{#if actions}` branch, so the noop fallback only satisfies types).
	/** The record's own handlers, aliased. `actions` is also the name of the hero's action SLOT,
	 *  and inside that snippet the snippet wins — so anything in it reads `record` instead. */
	const record = $derived(actions);
	const enhanceForm: Enhance = $derived(actions?.enhance ?? (() => {}));

	const c = $derived(detail.contact);
	/** The client's base currency. Headline figures use it; each account line uses its own. */
	const baseCurrency = $derived(c.currency || 'GBP');
	const isOrg = $derived(c.type === 'organisation');
	/** Person-level bank is the fallback for withdrawable products. A client who only holds
	 *  wrappers that refuse withdrawals (JISA by default) has no destination to store. */
	const bankDetailsAllowed = $derived(
		detail.accounts.length === 0 || detail.accounts.some((a) => a.bank_details_allowed !== false)
	);
	const name = $derived(clientName(c));
	// The client's public reference. `providerReference` strips the display prefix, the same
	// value the listing used to print under every name and the one a custodian asks for.
	const reference = $derived(providerReference(c.uid));
	const registeredContactRelationships = $derived(
		(detail.relationships ?? []).filter(
			(relationship) => relationship.relationship_type === 'registered_contact'
		)
	);
	const relationshipName = (relationship: (typeof registeredContactRelationships)[number]) =>
		relationship.other_type === 'organisation'
			? relationship.other_name || 'Organisation'
			: [relationship.other_title, relationship.other_first_name, relationship.other_surname]
					.filter(Boolean)
					.join(' ') ||
				relationship.other_name ||
				'Client';
	const closed = $derived(!!c.deleted_at);
	const recordStatus = $derived(
		clientStatus({
			account_count: detail.accounts.length,
			represented_account_count: registeredContactRelationships.length,
			deleted_at: c.deleted_at,
			provider_status: c.provider_status
		})
	);
	const closeable = $derived(!closed && canCloseClient(detail.accounts));
	const usersKnown = $derived(detail.users != null);
	const hasPortalAccess = $derived((detail.users?.length ?? 0) > 0);
	const canInvitePortal = $derived(
		!!actions?.invitePortal && usersKnown && !hasPortalAccess && !closed
	);
	const canPinClassification = $derived(!!actions?.pinClassification && classifications.length > 0);
	const canSetGroups = $derived(!!actions?.setGroups && groups.length > 0);
	/** Contact, pension and employer cards live on Details (and on `all`). Personal stays on
	 *  Overview as well so an adviser opening the record still sees who the person is — read-only
	 *  there; the pencil lives on Details, where the other person cards are. */
	const showDetailCards = $derived(section === 'details' || section === 'all');
	/** Figures, accounts, allocation and the work queue are Overview (and `all`). */
	const showOverviewMoney = $derived(section === 'overview' || section === 'all');
	/** The address on one line for the read view. Blank parts are dropped rather than rendered as
	 *  empty commas — a client with only a postcode reads as a postcode, not as ", , , SW1A 1AA". */
	const addressLine = $derived(formatStoredAddress(c.address));
	/** Shared look for the header's copyable facts: sit on the text row, no extra padding that
	 *  would read as a gap between email / mobile / address, pointer cursor from CopyButton. */
	const HEADER_COPY_CLASS =
		'text-muted-foreground h-auto px-0 py-0 text-xs hover:bg-transparent hover:text-foreground';
	/** A stored canonical value as a person would read it: `self_employed` -> "Self employed".
	 *  The onboarding mapping canonicalises the form's display label on the way IN (mapping.ts's
	 *  `snake`), so the record has to undo it on the way out rather than showing the wire value. */
	const label = (v: string) => v.replace(/_/g, ' ').replace(/^./, (ch) => ch.toUpperCase());
	// (`listed` went with `factFind`: the two array fields it flattened — nationality and tax
	//  residency — are rendered by `countryNames`, which resolves the ISO codes to names.)

	/** A stored number read back in the grouping the phone FIELD writes it in — the same
	 *  `formatPhoneNumber` (@graphene/ui masks), so a record cannot show `+447715215412` where the
	 *  form that captured it showed `+44 7715 215412`. Shape is the API's job and it does it
	 *  (`app.contacts.phone_expr` unwraps the Seccl provider object before this ever sees it);
	 *  this is spacing alone. A value with no digits in it is left exactly as it stands rather
	 *  than reduced to a bare "+". */
	const phoneText = (value: string | null | undefined): string | null => {
		const raw = (value ?? '').trim();
		if (!raw) return null;
		return /\d/.test(raw) ? formatPhoneNumber(raw) || raw : raw;
	};

	/** ISO-2 for Postcoder. Stored country may already be a code, or a display name from an older
	 *  free-text box. */
	function isoCountry(raw: string): string {
		const text = raw.trim();
		if (/^[A-Za-z]{2}$/.test(text)) return text.toUpperCase();
		if (!text) return 'GB';
		return countries.find((x) => x.name.toLowerCase() === text.toLowerCase())?.code ?? 'GB';
	}

	function storedToLookup(stored: unknown): Address | null {
		const row = stored as Record<string, unknown> | null | undefined;
		if (!row || typeof row !== 'object') return null;
		const line1 = String(row.line1 ?? '').trim();
		const city = String(row.city ?? '').trim();
		const postcode = String(row.postcode ?? '').trim();
		if (!line1 && !city && !postcode) return null;
		const countryRaw = String(row.country ?? '').trim();
		return {
			line1: String(row.line1 ?? ''),
			line2: row.line2 ? String(row.line2) : undefined,
			town: city,
			postcode,
			country: countryRaw,
			countryCode: isoCountry(countryRaw)
		};
	}

	function addressHasContent(pick: Address | null, stored: unknown): boolean {
		if (pick)
			return Boolean(
				pick.line1.trim() || pick.town.trim() || pick.postcode.trim() || (pick.line2 ?? '').trim()
			);
		return storedToLookup(stored) !== null;
	}

	function formatStoredAddress(stored: unknown): string {
		const row = stored as Record<string, unknown> | null | undefined;
		if (!row || typeof row !== 'object') return '';
		const countryRaw = String(row.country ?? '').trim();
		const country =
			countryRaw.length === 2
				? (countries.find((x) => x.code === countryRaw.toUpperCase())?.name ?? countryRaw)
				: countryRaw;
		return [row.line1, row.line2, row.city, row.postcode, country]
			.map((part) => String(part ?? '').trim())
			.filter(Boolean)
			.join(', ');
	}

	function formatPickedAddress(pick: Address): string {
		const country =
			countries.find((x) => x.code === pick.countryCode)?.name ?? pick.country ?? pick.countryCode;
		return [pick.line1, pick.line2, pick.town, pick.postcode, country]
			.map((part) => (part ?? '').trim())
			.filter(Boolean)
			.join(', ');
	}

	// ── THE RECORD IS FIVE SECTIONS, EACH SAVED ON ITS OWN ──────────────────────────────────────
	//
	// It was one "Details" card holding twenty-odd fields behind one pencil and one save. Two things
	// were wrong with that, and they pull in opposite directions:
	//
	//  · a save of ANY field was a save of ALL of them, so correcting a phone number rewrote the
	//    whole record — which is how a details save came to NULL the fact-find answers before the
	//    `data.has` rule was added, and why that rule now has to hold for five forms at once;
	//  · the read view DROPPED any field with no value, which reads well for a register of whatever
	//    happens to be recorded and reads terribly for a section a firm is meant to COMPLETE. A
	//    field nobody can see is a field nobody fills in — `relevant_uk_earnings` went five
	//    migrations with no editor while the HMRC relievable-limit check ran on the £3,600 floor.
	//
	// So: each section states EVERY one of its fields in both modes, with an em dash for the ones
	// nobody has answered, and saves only its own. One section is open at a time — five simultaneous
	// dirty forms over one record is five ways to lose an edit, and the pencil is per card so
	// "independently editable" is about what a save touches rather than how many can be open.
	let editingSection = $state<string | null>(null);
	const isEditing = (key: string) => editingSection === key;
	const pendingBank = $derived(
		pendingReviews.find((r) => r.action_key === 'contact.bank_details_changed') ?? null
	);
	/** Detail edits waiting for a second person. One key covers five cards, so there can be
	 *  several — each names the fields it holds — and the cards stay editable for the rest. */
	const pendingDetails = $derived(
		pendingReviews.filter((r) => r.action_key === 'client.details_changed')
	);
	const pendingVulnerability = $derived(
		pendingReviews.find((r) => r.action_key === 'client.vulnerability_updated') ?? null
	);
	/** The detail holds that touch one card, so a hold is said ON the card it will change — the
	 *  tax card used to read as unchanged while its change sat in a block four cards up. The rare
	 *  hold on a field no card owns keeps that block. */
	const holdsFor = (card: DetailCard): ReviewRow[] => reviewsForCard(pendingDetails, card);
	const pendingOffCards = $derived(reviewsOffTheCards(pendingDetails));
	const bankNeedsReview = $derived(justificationRequired.includes('contact.bank_details_changed'));
	/** Contact / personal / pension / employer share `updateClient`; bank is the money family. */
	// Editable WHILE a case waits: a second edit to the same card updates the open case rather
	// than being refused, so the pencil is never withheld for a hold.
	const canEditSection = (kind: 'client' | 'bank') =>
		kind === 'bank' ? !!actions?.updateBankAccount : !!actions?.updateClient;

	// ── The controls' own state ─────────────────────────────────────────────────────────────────
	//
	// STATE, not `$derived`, for the same reason the vulnerability drawer's fields are: these drive
	// what the form SHOWS (the flexi date) and what it POSTS, so they have to be able to hold the
	// adviser's pending answer rather than snapping back to the saved value the moment anything
	// else in the component re-runs. Seeded from the record and re-seeded whenever a different
	// section is opened, which is when a form is built.
	let addressPick = $state<Address | null>(null);
	let employerAddressPick = $state<Address | null>(null);
	let addressCountry = $state('GB');
	let employerAddressCountry = $state('GB');
	let editingContactAddress = $state(false);
	let editingEmployerAddress = $state(false);
	let nationalities = $state<string[]>([]);
	/** ISO codes, using the same closed multi-picker as nationality. Both columns are arrays and a
	 *  client may genuinely be tax-resident in more than one country. */
	let taxResidencies = $state<string[]>([]);
	// `null` UNTIL SOMEBODY TOUCHES THE CONTROL, and the record answers until then.
	//
	// Flexi-access date is gated on this, so seeding it in an `$effect` alone was wrong in a way
	// only SSR shows: effects do not run on the server, so the server rendered `false` for a
	// client who HAS taken flexi access and the date appeared only after hydration. Falling back
	// to the stored value makes the first paint right and still lets the switch drive the gate
	// live once it is used. Employer contributions is the same shape (a posted flag) but no
	// longer hides the Employer card — that section is always on for a pension client.
	let flexiAccessTaken = $state<boolean | null>(null);
	let employerContributions = $state<boolean | null>(null);
	const showFlexiDate = $derived(flexiAccessTaken ?? c.flexi_access_taken ?? false);
	const showEmployerSection = $derived(employerContributions ?? c.employer_contributions ?? false);
	/** Same shape for occupation: the detail box is only for Other and Retired. */
	let occupationDraft = $state('');
	/** The typed national identifier. Reseeded with the other drafts so a cancelled edit does not linger. */
	let nationalIdentifierDraft = $state('');
	/** ISO code from `ref.countries`, same vocabulary as nationality and tax residency. */
	let countryOfBirthDraft = $state('');
	/** Morgan Lloyd's closed list plus the client's own supporting statement. */
	let sourceOfWealthDraft = $state('');
	let sourceOfWealthDetailDraft = $state('');
	/** Yes or No; empty until the client has answered. */
	let usPersonDraft = $state('');
	/** The W-9 or W-8 on file, if any: a separate answer from `usPersonDraft`. */
	let fatcaStatusDraft = $state('');
	let sourceOfFundsDraft = $state('');
	let sourceOfFundsDetailDraft = $state('');
	/** The three inputs CONCAT is derived from, mirrored so it recomputes as they are typed rather
	 *  than only after a save. Seeded from the record with the rest. */
	let dobDraft = $state('');
	/** `SelectMenu` binds a value rather than reading a DOM default, so this joins the
	 *  other section drafts and is reseeded with them. */
	let taxReliefDraft = $state('');
	let genderDraft = $state('');
	let titleDraft = $state('');
	let maritalDraft = $state('');
	let employmentDraft = $state('');
	let firstNameDraft = $state('');
	let surnameDraft = $state('');
	let retirementAgeDraft = $state('');
	let bankSchemeDraft = $state('bacs');
	let bankNameDraft = $state('');
	let bankSortDraft = $state('');
	let bankNumberDraft = $state('');
	let bankIbanDraft = $state('');
	let bankBicDraft = $state('');
	let bankMethodDraft = $state('bacs');
	let bankErrors = $state<Partial<Record<keyof BankInput, string>>>({});
	let bankDirectoryStatus = $state<BankCheckStatus>('idle');
	let bankDirectoryResult = $state<BankCheckResult | null>(null);
	/** One set per person. The loader may pass a pre-read copy; otherwise read off the contact. */
	const bank = $derived(bankAccount ?? readBankDetails(c));
	// The employer's bank account (0271) — the scheme paying employer contributions in. The same
	// inputs and the same sort-code directory as the client's own; UK only, no rail or method,
	// because it is where money comes FROM, not where a withdrawal goes.
	const employerBank = $derived(readBankDetails({ bank_account: c.employer_bank_account }));
	let employerBankNameDraft = $state('');
	let employerBankSortDraft = $state('');
	let employerBankNumberDraft = $state('');
	let employerBankErrors = $state<Partial<Record<keyof BankInput, string>>>({});
	let employerBankStatus = $state<BankCheckStatus>('idle');
	let employerBankResult = $state<BankCheckResult | null>(null);
	const employerBankDigits = $derived.by(() => {
		const digits = bankDigits(employerBankNumberDraft);
		return digits.length === 7 ? digits.padStart(8, '0') : digits;
	});

	/** Every editable control, back to what the record says.
	 *
	 *  A FUNCTION, called from two effects, because the two things that must reseed are different
	 *  events: opening a section (a form abandoned with Cancel must not hand its half-typed
	 *  answers to the next one opened) and CHANGING CLIENT. Only the first was wired, and the
	 *  drafts are `bind:value`, so a Personal section left open while the reader clicked from
	 *  client A to client B still held A's date of birth, names and nationality — and saving
	 *  PATCHed them onto B, over the `dob` the pension projection and the over-75 relief check
	 *  both read. SvelteKit reuses this component between client records and there is no `{#key}`
	 *  above it, so `c` changing while `editingSection` does not is the ordinary case, not an
	 *  edge. The single form this replaced read `value={c.email}` straight off the record and
	 *  could not leak; the drafts are what made it possible. */
	function reseedDrafts(): void {
		addressPick = storedToLookup(c.address);
		employerAddressPick = storedToLookup(c.employer_address);
		addressCountry = isoCountry(
			String((c.address as Record<string, unknown> | null)?.country ?? '')
		);
		employerAddressCountry = isoCountry(
			String((c.employer_address as Record<string, unknown> | null)?.country ?? '')
		);
		editingContactAddress = false;
		editingEmployerAddress = false;
		nationalities = [...(Array.isArray(c.nationality) ? c.nationality : [])];
		taxResidencies = Array.isArray(c.tax_residency)
			? [...c.tax_residency]
			: c.tax_residency
				? [c.tax_residency]
				: [];
		flexiAccessTaken = null;
		employerContributions = null;
		occupationDraft = c.occupation ?? '';
		nationalIdentifierDraft = c.national_identifier ?? '';
		countryOfBirthDraft = c.country_of_birth ?? '';
		sourceOfWealthDraft = c.source_of_wealth ?? '';
		sourceOfWealthDetailDraft = c.source_of_wealth_detail ?? '';
		usPersonDraft = c.us_person == null ? '' : c.us_person ? 'Yes' : 'No';
		fatcaStatusDraft = c.fatca_status ?? '';
		sourceOfFundsDraft = c.source_of_funds ?? '';
		sourceOfFundsDetailDraft = c.source_of_funds_detail ?? '';
		dobDraft = c.dob ?? '';
		taxReliefDraft = c.tax_relief ?? '';
		genderDraft = c.gender ?? '';
		titleDraft = c.title ?? '';
		maritalDraft = c.marital_status ?? '';
		employmentDraft = c.employment_status ?? '';
		firstNameDraft = c.first_name ?? '';
		surnameDraft = c.surname ?? '';
		retirementAgeDraft = c.retirement_age != null ? String(c.retirement_age) : '';
		const storedBank = bankAccount ?? readBankDetails(c);
		bankSchemeDraft = bankSchemeOf(storedBank);
		bankNameDraft = storedBank?.name ?? '';
		bankSortDraft = storedBank?.sort_code ?? '';
		bankNumberDraft = storedBank?.account_number ?? '';
		bankIbanDraft = storedBank?.iban ?? '';
		bankBicDraft = storedBank?.bic ?? '';
		bankMethodDraft = storedBank?.method === 'faster_payment' ? 'faster_payment' : 'bacs';
		bankErrors = {};
		bankDirectoryStatus = 'idle';
		bankDirectoryResult = null;
		employerBankNameDraft = employerBank?.name ?? '';
		employerBankSortDraft = employerBank?.sort_code ?? '';
		employerBankNumberDraft = employerBank?.account_number ?? '';
		employerBankErrors = {};
		employerBankStatus = 'idle';
		employerBankResult = null;
	}

	$effect(() => {
		// Keyed on the open section so opening one re-seeds every control from the record.
		// `void` because a bare rune read is a statement with no effect as far as eslint is
		// concerned; the DEPENDENCY is the point, and this is the smallest way to state it.
		void editingSection;
		untrack(reseedDrafts);
	});

	/** Tax residency and country of birth read the country NAME — that is what those questions ask.
	 *  Nationality does NOT: see `nationalityOptions`. */
	const countryOptions = $derived(countries.map((x) => ({ value: x.code, label: x.name })));

	// ── THE NATIONAL IDENTIFIER IS DERIVED, NOT ASKED ───────────────────────────────────────────
	//
	// The regulator decides which identifier a person must give, and it decides from their
	// NATIONALITY: an EEA one wins, ties break on ISO code, and every country's list ends in CONCAT
	// for people with no national number. `ref.countries.identifiers` (0050) holds those lists.
	//
	// So the TYPE is not a text box. It was one — an adviser typed "NI number" or "NINO" or
	// "National insurance" into a column a report reads — and "NINO" is not the name of anything:
	// the United Kingdom's priority-0 entry reads **"National Insurance Number"**, which is what the
	// form now asks for and what is stored beside the digits.
	const byCountryCode = $derived(new Map(countries.map((x) => [x.code, x])));
	// Sorted BY LABEL here, which is this surface's own choice: the shared rule labels and
	// disambiguates in source order, so the onboarding fact-find's picker keeps its order and this
	// one reads alphabetically. Ordering is presentation; the labelling is the rule.
	const nationalityChoices = $derived(
		nationalityOptions(countries)
			.slice()
			.sort((a, b) => a.label.localeCompare(b.label, 'en-GB'))
	);
	const occupationChoices = OCCUPATION_OPTIONS.map((o) => ({ value: o, label: o }));
	const showOccupationDetail = $derived(
		occupationDraft === 'Other' || occupationDraft === 'Retired'
	);
	const occupationDetailLabel = $derived(
		occupationDraft === 'Retired' ? 'Previous occupation' : 'Please specify'
	);
	const sourceOfWealthChoices = $derived.by(() => {
		const listed = SOURCE_OF_WEALTH_OPTIONS as readonly string[];
		const base = listed.map((o) => ({ value: o, label: o }));
		// A value typed before this was a picker still has to round-trip, or the dropdown
		// would blank and a save would silently replace it with empty.
		return sourceOfWealthDraft && !listed.includes(sourceOfWealthDraft)
			? [{ value: sourceOfWealthDraft, label: label(sourceOfWealthDraft) }, ...base]
			: base;
	});
	const sourceOfFundsChoices = $derived.by(() => {
		const listed = SOURCE_OF_FUNDS_OPTIONS as readonly string[];
		const base = listed.map((o) => ({ value: o, label: o }));
		return sourceOfFundsDraft && !listed.includes(sourceOfFundsDraft)
			? [{ value: sourceOfFundsDraft, label: label(sourceOfFundsDraft) }, ...base]
			: base;
	});
	const usPersonChoices = [
		{ value: 'Yes', label: 'Yes' },
		{ value: 'No', label: 'No' }
	];
	/** W-9 or W-8. A stored code outside the pair (SEI's other forms) stays selectable. */
	const fatcaStatusChoices = $derived(
		[
			'w9',
			'w8',
			...(fatcaStatusDraft && !['w9', 'w8'].includes(fatcaStatusDraft) ? [fatcaStatusDraft] : [])
		].map((value) => ({ value, label: fatcaFormLabel(value) }))
	);
	/** The shared lists (`type=gender` / `marital` / `employment`). A value already on the
	 *  record outside the list stays selectable, so a save does not blank it. */
	const genderChoices = $derived(withStoredChoice(GENDER_OPTIONS, genderDraft));
	const maritalChoices = $derived(withStoredChoice(MARITAL_OPTIONS, maritalDraft));
	const employmentChoices = $derived(withStoredChoice(EMPLOYMENT_OPTIONS, employmentDraft));
	const closedLabel = (
		options: readonly { value: string; label: string }[],
		value: string | null | undefined
	): string | null => {
		if (!value) return null;
		return options.find((option) => option.value === value)?.label ?? label(value);
	};
	const identifierCountry = $derived(
		pickIdentifierCountry(taxResidencies.length ? taxResidencies : nationalities, byCountryCode)
	);
	const identifierSpec = $derived(pickIdentifierSpec(identifierCountry, byCountryCode));
	/** CONCAT is COMPUTED, never typed: the format exists so two firms holding one person derive
	 *  the same string, which a hand-typed one defeats. Recomputed live off the form's own name and
	 *  date fields, so it follows a correction rather than going stale. */
	const isConcat = $derived(identifierSpec?.name?.toUpperCase() === 'CONCAT');
	const concat = $derived(
		computeConcat({ dob: dobDraft, firstName: firstNameDraft, surname: surnameDraft })
	);
	const identifierValue = $derived(
		isConcat ? (concat ?? c.national_identifier ?? '') : (c.national_identifier ?? '')
	);
	const nationalIdentifierError = $derived(
		isConcat ? null : nationalInsuranceError(identifierSpec?.name, nationalIdentifierDraft)
	);

	/** POSTCODER SAYS `town`, OUR COLUMN SAYS `city`, and the mismatch is silent: `Address` has no
	 *  `city` key, so a naive lookup returned undefined and the picked address filled every field
	 *  except the town — which reads as Postcoder being flaky rather than as a key name. Mapped in
	 *  one place, here, for both addresses. */
	type AddressPart = 'line1' | 'line2' | 'city' | 'postcode' | 'country';
	const PICKED: Record<AddressPart, keyof Address> = {
		line1: 'line1',
		line2: 'line2',
		city: 'town',
		postcode: 'postcode',
		country: 'country'
	};
	const fieldFrom = (picked: Address | null, stored: unknown, key: AddressPart): string =>
		String(picked?.[PICKED[key]] ?? (stored as Record<string, unknown> | null)?.[key] ?? '');
	const employerAddressLine = $derived(formatStoredAddress(c.employer_address));

	/** A view row, always rendered — `null` becomes an em dash rather than vanishing.
	 *
	 *  `lines` is for a fact stored as a LIST rather than a value. The secondary number is one such:
	 *  the column is still an array, so the row prints its lines. It stays ONE row because the
	 *  register keys rows by their label and the card law is that a SECTION states each of its
	 *  FIELDS once in both modes. */
	type Row = {
		label: string;
		value: string | null;
		lines?: string[];
		/** A standalone money figure: the register renders it through `Money`; `value` stays the text. */
		money?: { amount: number; currency?: string };
		/** Where the value opens, when it names a record of its own. */
		href?: string;
	};
	const rows = (...entries: [string, unknown][]): Row[] =>
		entries.map(([label, value]) => ({
			label,
			value: value === null || value === undefined || value === '' ? null : String(value)
		}));
	/** The person-facts register. The signed W-9 or W-8 opens its document where the app has a route. */
	const complianceFacts = $derived.by(() => {
		const href =
			c.fatca_document && links.document ? links.document(c.fatca_document.uid) : undefined;
		return rows(...personComplianceFacts(c)).map((row) =>
			href && row.label === FATCA_DOCUMENT_FACT ? { ...row, href } : row
		);
	});

	/** Marks rows whose value is a standalone money figure, so the register renders them through `Money`. */
	const withMoney = (list: Row[], amounts: Record<string, number | null | undefined>): Row[] =>
		list.map((row) => {
			const amount = amounts[row.label];
			return amount == null ? row : { ...row, money: { amount } };
		});

	/** O(1) off `byCountryCode`, the memoised map the identifier logic already builds 60 lines
	 *  above — it was a `.find()` over all ~249 `ref.countries` rows, called once for country of
	 *  birth and once per code through `countryNames` for nationalities and tax residencies, and
	 *  re-run on every reactive re-render of the record (the five section gates, the drafts and
	 *  both address pickers all invalidate it). A client with two nationalities and a tax
	 *  residency cost ~1,000 string comparisons a render for an answer the map already holds. */
	const countryName = (code: string | null | undefined): string | null =>
		code ? (byCountryCode.get(code)?.name ?? code) : null;
	const countryNames = (codes: string[] | string | null | undefined): string | null => {
		const list = Array.isArray(codes) ? codes : codes ? [codes] : [];
		return list.length ? list.map((c) => countryName(c) ?? c).join(', ') : null;
	};
	const yesNo = (v: boolean | null | undefined): string | null =>
		v === null || v === undefined ? null : v ? 'Yes' : 'No';

	/** The three ticks, in the order a file is assembled: who they are, then whether they cleared
	 *  screening, then what they agreed to. */
	const COMPLIANCE_CHECKS = [
		{ kind: 'identity', title: 'Identity verified' },
		{ kind: 'aml', title: 'AML screened' },
		{ kind: 'terms', title: 'Terms accepted' }
	] as const;

	/** `crm.kyc_checks` by kind, for the Compliance card's three ticks. */
	const checkFor = (kind: string) => (detail.kyc ?? []).find((c) => c.kind === kind) ?? null;

	// NO `factFind` LIST. It was one flat register of every answered fact, in reading order, with
	// the unanswered ones DROPPED. Both halves are superseded: each fact is now stated by the
	// section that owns it (contact, personal, pension, employer, compliance), and each section
	// states its own fields whether or not they are filled in — which is the point, because a field
	// nobody can see is a field nobody fills in.

	/** The fact-find's overflow — answers with no column, nested under `data` by the mapping.
	 *  Flattened to `a.b.c` so a firm's own added questions are legible without this component
	 *  having to know any of them. */
	function flatten(value: unknown, prefix = ''): { label: string; value: string }[] {
		if (value === null || value === undefined || value === '') return [];
		if (Array.isArray(value)) return [{ label: prefix, value: value.join(', ') }];
		if (typeof value === 'object') {
			return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) =>
				flatten(v, prefix ? `${prefix}.${k}` : k)
			);
		}
		return [
			{ label: prefix, value: typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value) }
		];
	}
	const gathered = $derived(
		flatten(c.data ?? null)
			// `created_via` is our own bookkeeping, not something anyone asked the client.
			.filter((row) => row.label !== 'created_via')
			.map((row) => ({ label: label(row.label.split('.').pop() ?? row.label), value: row.value }))
	);

	const aum = $derived(detail.accounts.reduce((s, a) => s + (a.value ?? 0), 0));

	// Vulnerability (FCA FG21/1, 0054). The header states it on every page, because it changes
	// how the whole conversation should be conducted — an adviser who has to click to find it
	// will sometimes not click. The Details band also files it with the other person cards, so
	// it can be read and recorded in the same place as the rest of the record. All applicable
	// drivers show, not a primary one: the guidance is explicit that they compound.
	const vulnerable = $derived(Boolean(c.is_vulnerable));
	const vulnReasons = $derived(c.vulnerability_reasons ?? []);
	const vulnTemporary = $derived(c.vulnerability_is_temporary ?? null);
	const vulnerabilityDrivers = $derived(
		vulnReasons.length
			? vulnReasons.map((reason) => VULNERABILITY_LABELS[reason] ?? reason).join(' · ')
			: null
	);
	const vulnerabilityCharacteristics = $derived.by(() => {
		const recorded = c.vulnerability_characteristics ?? [];
		return recorded.length
			? recorded.map((slug) => VULNERABILITY_CHARACTERISTIC_LABELS[slug] ?? slug).join(' · ')
			: null;
	});
	const vulnerabilityDuration = $derived(
		vulnTemporary === true ? 'Temporary' : vulnTemporary === false ? 'Permanent' : null
	);
	const vulnRecorded = $derived(
		c.vulnerability_recorded_at ? formatDate(c.vulnerability_recorded_at) : null
	);
	let openVulnerability = $state(false);
	let savingVulnerability = $state(false);
	// The drawer's fields are STATE, not one-way props off `detail`: the flag gates the rest, so
	// turning it off has to be able to empty them. Seeded on open rather than derived — a `$derived`
	// would snap each control back to the saved value the moment anything else re-ran.
	let vulnFlag = $state(false);
	let vulnDrivers = $state<VulnerabilityReason[]>([]);
	let vulnCharacteristics = $state<string[]>([]);
	/** The Duration segment, as the hidden field's string. `undefined` = nothing selected = the
	 *  API's `null` ("not established"). */
	let temporaryChoice = $state<string | undefined>(undefined);
	let vulnNotes = $state('');

	function editVulnerability() {
		vulnFlag = vulnerable;
		vulnDrivers = [...vulnReasons];
		vulnCharacteristics = [...(c.vulnerability_characteristics ?? [])];
		temporaryChoice = durationChoice(vulnTemporary);
		vulnNotes = c.vulnerability_notes ?? '';
		openVulnerability = true;
	}

	/** Turning the flag off empties the block it describes, rather than leaving a greyed-out driver
	 *  still ticked — which would show the record holding something it does not. The API clears all
	 *  of it for `is_vulnerable: false` regardless, so this makes the form say what will happen. */
	/** The four drivers as typed pairs -- `Object.entries` widens the keys to `string`, and
	 *  everything downstream (the state array, the toggle) is better off with the union. */
	const DRIVER_ENTRIES = Object.entries(VULNERABILITY_LABELS) as [VulnerabilityReason, string][];

	function clearVulnerabilityDetail() {
		vulnDrivers = [];
		vulnCharacteristics = [];
		temporaryChoice = undefined;
		vulnNotes = '';
	}

	function toggleDriver(value: VulnerabilityReason, on: boolean) {
		vulnDrivers = toggleReason(vulnDrivers, value, on);
	}

	// Accounts use the shared DataTable so they sort and read like every other listing, and each row
	// links through to the account detail page (which owns that account's holdings).
	let accountSort = $state<DataTableSort | null>(null);

	// SEARCH + FILTERS over this client's accounts, local because the whole set is already here —
	// `detail.accounts` is the client's accounts in full, so narrowing them client-side is the truth
	// rather than a slice of a slice (which is why the firm-wide /accounts book has to ask the API).
	//
	// Options come from the rows on screen (`optionsFrom`), so a filter can never offer a value that
	// would return nothing — the same rule the book listings follow.
	let accountQuery = $state('');
	let accountStatusFilter = $state<string[]>([]);
	let accountProductFilter = $state<string[]>([]);
	const accountStatusOptions = $derived(optionsFrom(detail.accounts, (a) => a.status, humanise));
	const accountProductOptions = $derived(
		optionsFrom(detail.accounts, (a) => a.product_type, humanise)
	);
	const filterDefs = $derived(
		accountFilterDefs({ statusOptions: accountStatusOptions, typeOptions: accountProductOptions })
	);
	const accountFilterValue = $derived<Record<string, string[]>>({
		...(accountStatusFilter.length ? { status: accountStatusFilter } : {}),
		...(accountProductFilter.length ? { type: accountProductFilter } : {})
	});
	function onAccountFilters(next: Record<string, string[]>) {
		accountStatusFilter = next.status ?? [];
		accountProductFilter = next.type ?? [];
	}
	const accountsDirty = $derived(accountStatusFilter.length > 0 || accountProductFilter.length > 0);
	function clearAccountFilters() {
		accountStatusFilter = [];
		accountProductFilter = [];
	}
	const visibleAccounts = $derived(
		detail.accounts.filter(
			(a) =>
				matches(accountStatusFilter, a.status) &&
				matches(accountProductFilter, a.product_type) &&
				// The model is searchable but not a filter: a client's accounts rarely span enough models
				// for a picker to earn its width, and typing the name is the faster way in either case.
				search(
					accountQuery,
					a.name,
					providerReference(a.uid),
					a.product_name,
					a.product_type,
					a.model_name
				)
		)
	);
	const accountColumns: DataTableColumn<ClientAccountRow>[] = [
		{
			key: 'name',
			header: 'Account',
			sortable: true,
			sortDefault: true,
			// `CELL_SPINE` — `w-full max-w-0`. The cell had `truncate` and NOTHING to overflow, which
			// is failure mode 1 in text-shell.ts: with no ceiling the box grows instead and the class
			// is inert, so a 132-character account name set the column's width and the table scrolled
			// sideways. `max-w-0` looks wrong and is the point: under `table-layout: auto` it is what
			// lets the cell shrink below its content, and `w-full` then claims whatever the other
			// columns did not use. This is the identity column, so it is the one that should give way.
			cellClass: CELL_SPINE,
			sortValue: (a) => a.name ?? providerReference(a.uid) ?? ''
		},
		// Service type · Type · Model — the wrapper tag is what a reader scans a person's
		// accounts by, so the full product name is gone. Status is last: a state, not a fact
		// you compare on the way to the money. Under `table-layout: auto` the identity column
		// (`CELL_SPINE`) takes leftover width; the rest fit their content.
		{
			key: 'service',
			header: 'Service type',
			sortable: true,
			hideBelow: 'lg',
			sortValue: (a) => a.service_type
		},
		{
			key: 'type',
			header: 'Type',
			sortable: true,
			sortValue: (a) => a.product_type
		},
		{
			key: 'model',
			header: 'Model',
			sortable: true,
			hideBelow: 'md',
			// Left-aligned, fit-to-content, capped so a long model name ellipsises rather than
			// blowing the row. `CELL_MODEL` is the ceiling.
			cellClass: CELL_MODEL,
			sortValue: (a) => a.model_name
		},
		{
			key: 'value',
			header: 'Value',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (a) => a.value
		},
		{
			key: 'cash',
			header: 'Cash',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelow: 'md'
		},
		{
			key: 'growth',
			header: 'Growth',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			hideBelow: 'md'
		},
		{ key: 'status', header: 'Status', sortable: true }
	];

	// ── Money-movement actions ─────────────────────────────────────────────────
	type ActionMeta = {
		label: string;
		title: string;
		blurb: string;
		cta: string;
		needsModel?: boolean;
		needsDue?: boolean;
	};
	const ACTIONS: Record<InstructionKind, ActionMeta> = {
		expected_in: {
			label: 'Record money in',
			title: 'Record expected money in',
			blurb:
				'Log an incoming contribution or transfer you’re expecting. Records the expectation only.',
			cta: 'Record expectation',
			needsDue: true
		},
		withdrawal: {
			label: 'Withdraw',
			title: 'Withdraw cash',
			blurb: 'Request a cash withdrawal. Checked against the account’s available balance.',
			cta: 'Request withdrawal'
		},
		model_topup: {
			label: 'Top up model',
			title: 'Top up against a model',
			blurb: 'Invest additional cash into the account’s discretionary model.',
			cta: 'Record top-up',
			needsModel: true
		},
		model_withdrawal: {
			label: 'Withdraw from model',
			title: 'Withdraw from a model',
			blurb: 'Disinvest from the account’s discretionary model back to cash.',
			cta: 'Record withdrawal',
			needsModel: true
		},
		adhoc_fee: {
			label: 'Ad hoc fee',
			title: 'Ad hoc fee',
			blurb: 'Charge a one-off fee from the account’s available cash.',
			cta: 'Record fee'
		}
	};
	let openAction = $state<InstructionKind | null>(null);

	// Pending flags for the page's submit buttons.
	let saving = $state(false);
	let noting = $state(false);
	let instructionSubmitting = $state(false);
	let inviting = $state(false);
	// ── Read first, edit on request ───────────────────────────────────────────
	// A client record is READ far more often than it is edited, and a permanent form says the
	// opposite: every value sitting in an input reads as unsaved work, and an adviser checking a
	// postcode has to look at a page that appears to be mid-edit. So each editable panel opens in
	// view mode and carries its own pencil, and the "Edit details" button is gone from the action
	// bar — an action bar is for things that happen to a client, not for scrolling to a form.
	//
	// Per panel, not per page: bank details and the profile are separate saves against separate
	// endpoints, and one pencil for both would arm a form the adviser did not mean to touch.

	// ── Bank details ───────────────────────────────────────────────────────────
	// Validated in the browser so the adviser gets a field message without a round trip; the API
	// re-validates and stays the authority (app/contacts.py::normalise_bank_account).
	/** A held save is not a save. The toast names the case, and says when the edit JOINED a case
	 *  already waiting rather than opening another — the second edit to a card with an open case
	 *  used to be refused with a 409; now it updates the case. */
	const heldLabel = (d?: Record<string, unknown>): string | null => {
		if (!d?.pending_review) return null;
		const ref = reviewCaseRef(typeof d.case_no === 'number' ? d.case_no : null);
		return d.amended ? `Review ${ref} updated` : `Sent for review · ${ref}`;
	};
	const savedOrHeld = (saved: string) => (d?: Record<string, unknown>) => heldLabel(d) ?? saved;
	const bankToast = withToast(
		(d) => heldLabel(d) ?? (d?.cleared ? 'Bank details cleared' : 'Bank details saved'),
		{
			pending: (v) => (saving = v),
			reset: false,
			onSuccess: () => (editingSection = null)
		}
	);
	let bankJustification = $state('');
	let bankJustificationError = $state('');
	const onBankSubmit: EnhanceSubmit = (input) => {
		const field = (k: string) => String(input.formData.get(k) ?? '');
		const checked = validateBank({
			scheme: field('bank_scheme') === 'iban' ? 'iban' : 'bacs',
			name: field('bank_name'),
			sort_code: field('bank_sort_code'),
			account_number: field('bank_account_number'),
			iban: field('bank_iban'),
			bic: field('bank_bic'),
			method: field('bank_method') === 'faster_payment' ? 'faster_payment' : 'bacs'
		});
		if (!checked.ok) {
			bankErrors = { [checked.field]: checked.error };
			input.cancel();
			return;
		}
		const directory = bankDirectoryError(
			field('bank_scheme') === 'iban' ? 'iban' : 'bacs',
			bankDirectoryStatus,
			bankDirectoryResult?.state,
			bankDirectoryResult?.message
		);
		if (directory) {
			bankErrors = { [directory.field]: directory.error };
			input.cancel();
			return;
		}
		if (bankNeedsReview && !field('justification').trim()) {
			bankJustificationError =
				'Explain why this change is needed — a second person will review it.';
			input.cancel();
			return;
		}
		bankErrors = {};
		bankJustificationError = '';
		return bankToast(input);
	};

	const employerToast = withToast(savedOrHeld('Employer details updated'), {
		pending: (v) => (saving = v),
		reset: false,
		onSuccess: () => (editingSection = null)
	});
	/** The Employer card's save: when any bank field was typed, the pair must be a valid UK pair the
	 *  sort-code directory accepts, as on the client's own bank card; blank clears it. */
	const onEmployerSubmit: EnhanceSubmit = (input) => {
		const field = (k: string) => String(input.formData.get(k) ?? '');
		const typed = ['employer_bank_name', 'employer_bank_sort_code', 'employer_bank_account_number']
			.map(field)
			.some((v) => v.trim());
		if (typed) {
			const checked = validateBank({
				scheme: 'bacs',
				name: field('employer_bank_name'),
				sort_code: field('employer_bank_sort_code'),
				account_number: field('employer_bank_account_number'),
				iban: '',
				bic: '',
				method: 'bacs'
			});
			if (!checked.ok) {
				employerBankErrors = { [checked.field]: checked.error };
				input.cancel();
				return;
			}
			const directory = bankDirectoryError(
				'bacs',
				employerBankStatus,
				employerBankResult?.state,
				employerBankResult?.message
			);
			if (directory) {
				employerBankErrors = { [directory.field]: directory.error };
				input.cancel();
				return;
			}
		}
		employerBankErrors = {};
		return employerToast(input);
	};

	// Cancel-instruction confirmation: the row button arms the uid, the dialog submits
	// the hidden form so the ?/cancelInstruction action wiring is unchanged.
	let cancelUid = $state<string | null>(null);
	let cancelOpen = $state(false);
	let cancelForm = $state<HTMLFormElement | null>(null);

	// Close confirmation. The record stays loadable afterwards (Closed is a status on the
	// header), so there is no navigation away — invalidateAll is enough.
	let closeOpen = $state(false);
	let closing = $state(false);
	let closeForm = $state<HTMLFormElement | null>(null);
	let inviteForm = $state<HTMLFormElement | null>(null);
	let pinForm = $state<HTMLFormElement | null>(null);
	let pinUid = $state('');
	async function pinBook(uid: string) {
		pinUid = uid;
		await tick();
		pinForm?.requestSubmit();
	}
	let groupsForm = $state<HTMLFormElement | null>(null);
	let groupUids = $state<string[]>([]);
	async function toggleGroup(uid: string) {
		const current = new Set(assignedGroups.map((group) => group.uid));
		if (current.has(uid)) current.delete(uid);
		else current.add(uid);
		groupUids = [...current];
		await tick();
		groupsForm?.requestSubmit();
	}
	let restoreForm = $state<HTMLFormElement | null>(null);

	// THE ACTION BAR IS GONE. It was a row of eight outline buttons under the header — four money
	// movements, an illustration, Record vulnerability, Deactivate and Archive — which made the first
	// thing on a client's record a list of things you could do to them rather than anything about
	// them. Money movement belongs where it lands: an instruction targets an ACCOUNT, and the account
	// record already offers the same four against the account you are looking at, so this was a second
	// entry point that had to ask which account first.
	//
	// The rare ones — Record vulnerability, Invite, Close / Reopen — live in the header's overflow
	// menu. They are rare, deliberate and recoverable, which is what an overflow menu is for.
	//
	// Starting a journey for this client lives there too: the flows the app says this reader may
	// start, each already pointing at the kickoff with this client chosen.
	const newJourneys = $derived(links.newJourneys ?? []);
	const overflowActions = $derived(
		newJourneys.length > 0 ||
			(!!actions &&
				!!(
					actions.setVulnerability ||
					canInvitePortal ||
					(actions.closeClient && closeable) ||
					(actions.restoreClient && closed)
				))
	);

	// THE ILLUSTRATION DID NOT HAVE ANOTHER HOME, AND LOST ITS ONLY ONE. It went with the bar on the
	// reasoning above, but that reasoning was about money movement: an instruction belongs to an
	// account, so the account record could take it. An illustration belongs to nothing — it is a
	// projection for a PERSON, run with no account at all (`toIllustrationPayload` sends
	// `account_id: null`, because an illustration is what a client is shown BEFORE deciding to open a
	// pension). So there was nowhere for it to move to, and it simply stopped being reachable while
	// the drawer, the proxy route, the endpoint and the `onillustrate` contract all stayed wired.
	//
	// It is a visible button rather than an overflow item because it is routine adviser work, not a
	// rare and recoverable act on the record — the overflow menu's own criterion. Sitting beside
	// Close is how it got lost the first time.
	const illustrateAction = $derived(actions?.onillustrate ?? null);
	// A mount with no handler links out to whatever section hosts the form instead. Neither ⇒ no
	// button at all: generating is `illustrations:write`, and the app passes neither without it.
	const illustrationHref = $derived(illustrateAction ? null : (links.illustrations ?? null));
	const canIllustrate = $derived(!!illustrateAction || !!illustrationHref);
	// Looking at the client's own portal is routine adviser work too — "what are they seeing?" is
	// the question behind half the calls a firm takes — so it sits beside the illustration rather
	// than in the overflow. The app passes the handler only for a reader who already reaches this
	// client; the API re-checks that when it opens the session. A client with no login has nothing
	// to open, so the button waits until a profile is linked.
	const viewAsAction = $derived(actions?.onviewas ?? null);
	const actionAccounts = $derived(
		openAction && ACTIONS[openAction].needsModel
			? detail.accounts.filter((a) => a.model_id != null)
			: detail.accounts
	);

	// Close the action dialog on a successful submit; the list refreshes via the app's update.
	const onInstructionSubmit = withToast(
		(d) => (d?.pending_review ? 'Sent for review' : 'Instruction recorded'),
		{
			pending: (v) => (instructionSubmitting = v),
			onSuccess: () => (openAction = null)
		}
	);

	// ── Rail widgets: allocation ring, expected-in, models ─────────────────────
	// The ring's geometry (including the "Other" bucket for the tail past five classes) lives in
	// ./allocation so "the percentages add up to 100" is asserted rather than assumed.
	const allocSegments = $derived(allocationSegments(allocations));
	const expectedIn = $derived(
		instructions.filter((i) => i.kind === 'expected_in' && i.status === 'pending')
	);
	const expectedTotal = $derived(expectedIn.reduce((s, i) => s + i.amount, 0));
	/** The account a pending payment lands in. Its currency, else the client's. */
	function accountCurrency(accountId: number): string {
		return detail.accounts.find((account) => account.id === accountId)?.currency || baseCurrency;
	}
	/** One total only when every pending payment shares a currency. A mixed book is not a sum. */
	const expectedCurrency = $derived.by(() => {
		if (!expectedIn.length) return baseCurrency;
		const codes = expectedIn.map((item) => accountCurrency(item.account_id));
		return codes.every((code) => code === codes[0]) ? codes[0] : null;
	});
	// Flattened across every account (./standing.ts owns the ordering and the three labels).
	const standing = $derived(standingRows(detail.accounts));
	const standingColumns = $derived<DataTableColumn<StandingRow>[]>([
		{ key: 'direction', header: 'Type', sortable: true, sortValue: (r) => r.direction },
		{
			key: 'account',
			header: 'Account',
			cellClass: CELL_SPINE,
			sortable: true,
			sortValue: (r) => r.accountName
		},
		{ key: 'account_id', header: 'Account ID', hideBelow: 'md' },
		{
			key: 'schedule',
			header: 'Payment day',
			sortable: true,
			// Sorted by the DAY, not by the rendered sentence: "1st" sorts after "14th" as text.
			sortValue: (r) => r.payment_day ?? 0
		},
		{
			key: 'amount',
			header: 'Amount',
			align: 'right',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (r) => r.amount ?? 0
		},
		{ key: 'auto_invest', header: 'Auto-invest', hideBelow: 'lg' }
	]);
	// The two people popouts — Team's overflow menu and the portal-access badge beside the name.
	// Each shows what is attached and, with the action for it, one row to attach another; they are
	// separate dialogs rather than one "People" sheet with two sections because they are opened
	// from two different places for two different reasons, which is what the page they replaced
	// got wrong.
	let openAdvisers = $state(false);
	let teamOpen = $state(false);
	let openLogins = $state(false);
	let assigningAdviser = $state(false);
	let linkingUser = $state(false);
	let removingAdviser = $state<string | null>(null);
	let removingUser = $state<string | null>(null);
	let adviserSel = $state('');
	let teamRole = $state<ClientTeamRole>(DEFAULT_CLIENT_TEAM_ROLE);
	let loginSel = $state('');

	/** The firm's people who hold a given portal, minus those already attached — a person cannot be
	 *  assigned twice, and offering them is an error the API would have to refuse. Requires an
	 *  ACTIVE membership row: that status is the whole of portal access (docs/auth.md), so a
	 *  deactivated adviser/client still carries the row (never deleted) and matching on `portal`
	 *  alone kept offering someone long after they lost access to it. */
	const memberItems = (portal: 'platform' | 'client', taken: Set<string>) =>
		members
			.filter(
				(m) =>
					m.portals.some((pp) => pp.portal === portal && pp.status === 'active') &&
					!taken.has(m.user_id)
			)
			.map((m) => ({
				value: m.user_id,
				label: m.name ? `${m.name} (${m.email})` : (m.email ?? '')
			}));

	/** The client's team, adviser first — the order every list on this record shows it in. */
	const team = $derived(orderClientTeam(detail.advisers));

	/** Who could hold the picked role at all — separated from `teamItems` (below) so the empty
	 *  state can tell "nobody eligible" apart from "everyone eligible already holds this role":
	 *  both read as an empty picker, and only one of them means the firm has someone to set up
	 *  first. Adviser needs the adviser flag; the other roles need a member of staff. */
	const eligibleForRole = $derived(members.filter((m) => canHoldClientTeamRole(m, teamRole)));
	const teamItems = $derived(clientTeamPickerItems(members, detail.advisers, teamRole));
	/** "Ann stays on the team as Support." — only while a new adviser would replace the current. */
	const adviserNote = $derived(replacedAdviserNote(detail.advisers, teamRole, adviserSel));
	const teamRoleWord = $derived(clientTeamRoleLabel(teamRole) ?? 'Adviser');

	function pickTeamRole(next: string): void {
		if (!isClientTeamRole(next)) return;
		teamRole = next;
		// The person picked for the old role may not be offered for the new one (a paraplanner
		// cannot be the adviser without the flag), and a hidden stale value would post them anyway.
		if (!clientTeamPickerItems(members, detail.advisers, next).some((i) => i.value === adviserSel))
			adviserSel = '';
	}
	const loginItems = $derived(
		memberItems('client', new Set((detail.users ?? []).map((u) => u.user_id)))
	);

	// Notes used to try to align its card with Details' — measured live. Dropped, and now doubly
	// moot: the rail's fixed-height cards above it (Screening and Models) are both gone, so there is
	// nothing left for a pulled-up Notes to collide with, and the clamp that silently gave up was
	// only ever compensating for them.

	// Notes pagination — page of 10, expand/collapse in place, no inner scroll. The list used to
	// cap at 32rem and scroll inside the card, which hid notes behind a second scrollbar in a
	// rail that already pages. More than ten is a pager, not a scroll.
	const NOTES_PAGE_SIZE = 10;
	let notesPage = $state(1);
	// Local list so a just-added note appears without waiting for the layout load to re-run.
	// `update()` / invalidateAll does not always refresh `detail.notes` (the list lives on the
	// parent layout, and Details posts to the overview action), so the composer used to toast
	// "Note added" against the same empty rail until a full refresh.
	let notes = $state<ClientNoteRow[]>(untrack(() => detail.notes));
	const notesPageCount = $derived(Math.max(1, Math.ceil(notes.length / NOTES_PAGE_SIZE)));
	const notesPageItems = $derived(
		notes.slice((notesPage - 1) * NOTES_PAGE_SIZE, notesPage * NOTES_PAGE_SIZE)
	);
	const notesRangeLabel = $derived(
		rangeLabelFromOffset((notesPage - 1) * NOTES_PAGE_SIZE, notesPageItems.length, notes.length)
	);

	function asNote(raw: unknown): ClientNoteRow | null {
		if (!raw || typeof raw !== 'object') return null;
		const n = raw as Record<string, unknown>;
		if (typeof n.body !== 'string') return null;
		const id = typeof n.id === 'number' ? n.id : Number(n.id);
		if (!Number.isFinite(id)) return null;
		return {
			id,
			uid: typeof n.uid === 'string' ? n.uid : undefined,
			body: n.body,
			author: typeof n.author === 'string' ? n.author : null,
			created_at: typeof n.created_at === 'string' ? n.created_at : new Date().toISOString()
		};
	}

	// A long note previews clamped to two lines with an ellipsis; clicking the row expands it in
	// place. Plain array + reassignment (not a mutated Set) so Svelte's own reactivity picks up
	// the change without needing `svelte/reactivity`'s `SvelteSet` — this list is never more than
	// a page of notes long, so the O(n) filter/spread on toggle costs nothing.
	let expandedNoteIds = $state<number[]>([]);
	function toggleNote(id: number): void {
		expandedNoteIds = expandedNoteIds.includes(id)
			? expandedNoteIds.filter((x) => x !== id)
			: [...expandedNoteIds, id];
	}

	// Only a note whose two-line CLAMPED height is actually shorter than its full content
	// height gets the click-to-expand affordance — a short note showing the chevron/cursor for
	// an expand that would change nothing is worse than no affordance at all. Measured (not
	// guessed from character count: font, kerning and the rail's own width all affect how much
	// text two lines actually hold) via a plain ref bag rather than `$state` — these are DOM
	// nodes read imperatively inside the effect below, not values Svelte needs to react to.
	//
	// Deliberately keyed on `notesPageItems` alone, NOT `expandedNoteIds`: every note starts
	// collapsed (clamped) on a fresh page, which is the only state `scrollHeight >
	// clientHeight` can be measured in — reading `expandedNoteIds` here would rerun this on
	// every toggle and try to measure an already-expanded (unclamped) paragraph, which never
	// overflows itself and would wrongly drop its own chevron the moment it was opened. Not
	// re-measured on window resize (a rail narrow enough to change whether 2 lines holds a
	// given note, mid-session, on this one panel, is a real but rare edge case) — noted rather
	// than silently assumed away.
	let noteBodyEls: Record<number, HTMLParagraphElement> = $state({});
	let overflowingNoteIds = $state<number[]>([]);
	$effect(() => {
		const ids = notesPageItems.map((n) => n.id);
		queueMicrotask(() => {
			overflowingNoteIds = ids.filter((id) => {
				const el = noteBodyEls[id];
				return !!el && el.scrollHeight > el.clientHeight + 1;
			});
		});
	});

	// SelectMenu is string-valued: numeric ids convert at the payload boundary (String in,
	// the server action Numbers them back out).
	const SERVICE_ITEMS = [
		{ value: '', label: '—' },
		{ value: 'advised', label: 'Advised' },
		{ value: 'execution_only', label: 'Execution-only' },
		{ value: 'discretionary', label: 'Discretionary' }
	];
	/** An organisation's kind, from the shared vocabulary so the picker, the column and the read
	 *  view offer and show the same words. The blank is "an organisation of no stated kind". */
	const SUB_TYPE_ITEMS = [{ value: '', label: '—' }, ...ORGANISATION_SUB_TYPES];
	/** The human label for a stored service_type — one place, so the badge, the read view and the
	 *  edit form cannot disagree (a Discretionary client read as 'Advised' was the bug here). */
	const serviceLabel = (v: string | null | undefined) =>
		SERVICE_ITEMS.find((i) => i.value === v)?.label ?? 'Advised';

	// NO `detailRows`, and no Service field anywhere on this record. The single register it built is
	// five per-section ones now; SERVICE is gone because it is an ACCOUNT-level fact — a client can
	// be advised on one account and execution-only on another, so one value on the person was
	// either a summary that could be wrong or a default nothing enforced. The column and
	// `serviceLabel` stay: the listing still shows it and the API still returns it. The header
	// badges the stored value until an account's own service type is what the reader should trust.
	let subType = $state(untrack(() => detail.contact.sub_type ?? ''));
	let insAccount = $state('');
	let insAmount = $state<number | null>(null);
	let withdrawalUkMethod = $state<'bacs' | 'faster_payment'>('bacs');
	let passRailFee = $state(true);
	// SvelteKit reuses this component when moving between client records — reseed the
	// per-client picker state when the contact changes.
	let seededFor = $state(untrack(() => detail.contact.id));
	$effect(() => {
		if (c.id !== seededFor) {
			seededFor = c.id;
			subType = c.sub_type ?? '';
			// CLOSE THE OPEN FORM AND RESEED. A section left open belongs to the client it was
			// opened on: keeping it open over a different record shows one person's answers under
			// another person's name, and saving writes them there. Closing alone is not enough —
			// if nothing was open, `editingSection` does not change and the effect above does not
			// fire — so the drafts are reseeded here too rather than relying on that.
			editingSection = null;
			untrack(reseedDrafts);
			// Notes' own per-client state (below) needs the same reseed: `notesPage` staying
			// at, say, 3 while the new client has only one page of notes leaves `notesPageItems`
			// slicing out of range — an empty list, no pager (notesPageCount has already collapsed
			// to 1) to click back to page 1 with, and no reload short of a full page refresh.
			notesPage = 1;
			notes = detail.notes;
			expandedNoteIds = [];
			overflowingNoteIds = [];
		}
	});
	// Preselect the first eligible account whenever an action dialog opens (mirrors the
	// old native select's default).
	$effect(() => {
		if (openAction) {
			insAccount = String(actionAccounts[0]?.id ?? '');
			insAmount = null;
		}
	});
	// A withdrawal starts on the method saved for the selected account, and passing the fee on
	// is the default. Changing the account restarts both; changing the method does not.
	$effect(() => {
		if (openAction !== 'withdrawal') return;
		const account = detail.accounts.find((a) => String(a.id) === insAccount);
		const bank = withdrawalDestination({
			bank_accounts: account?.bank_accounts,
			client: { bank_account: c.bank_account }
		}).bank;
		withdrawalUkMethod = ukWithdrawalMethod(bank);
		passRailFee = true;
	});
	// The account and what it holds — and NOT its model, which used to be a third clause here
	// (`Pension — £290,167.38 · Marlborough MPS Passive Risk Grade 4`). That overran the trigger in a
	// 28rem dialog, and no ceiling fixes it: `SelectMenu` truncates correctly, so a cap just clipped
	// the model name off the end and left a line that looked complete. The model earned no room --
	// only the two model actions filter to discretionary accounts, an account has exactly ONE model,
	// and two accounts are told apart by their NAME, so the clause could never disambiguate a choice.
	// The value can and does, which is why it stays: it is the figure the adviser checks before
	// committing. The model is stated where it is a fact rather than a label — on the account row and
	// on its record.
	const withdrawalAccount = $derived(
		detail.accounts.find((a) => String(a.id) === insAccount) ?? null
	);
	const withdrawalBank = $derived(
		withdrawalDestination({
			bank_accounts: withdrawalAccount?.bank_accounts,
			client: { bank_account: c.bank_account }
		}).bank
	);
	const withdrawalInternational = $derived(
		Boolean(withdrawalBank) && bankSchemeOf(withdrawalBank) === 'iban'
	);
	const withdrawalMethod = $derived<WithdrawalPaymentMethod>(
		withdrawalInternational ? 'swift' : withdrawalUkMethod
	);
	const withdrawalTariff = $derived(
		openAction === 'withdrawal'
			? railFeeForMethod(withdrawalMethod, withdrawalAccount?.withdrawal_fees)
			: 0
	);
	const withdrawalClientFee = $derived(clientBorneRailFee(withdrawalTariff, passRailFee));
	const withdrawalTotal = $derived(withdrawalTotalDebit(insAmount, withdrawalClientFee));
	const accountItems = $derived(
		actionAccounts.map((a) => ({
			value: String(a.id),
			label: `${a.name ?? providerReference(a.uid)} — ${formatMoney(a.value ?? 0, a.currency || baseCurrency)}`
		}))
	);

	// What the selected account can actually fund, on the same basis the gate uses: the provider's
	// own leg, net of what this account's pending instructions have already claimed. Stated in the
	// dialog so a refusal is a figure the adviser already saw — the Cash figure in the header is
	// scaled to AUM for the allocation ring and is NOT what a withdrawal is checked against.
	const fundingFor = $derived(funding.find((f) => f.accountId === Number(insAccount)) ?? null);
	// Assembled as one string rather than interpolated markup: the separators have to sit inside the
	// text, because whitespace around a Svelte `{#if}` boundary collapses and the stamp rendered as
	// "£28,438.00 cash available· as at 2025-09-25".
	const headroom = $derived.by(() => {
		if (!openAction || openAction === 'expected_in' || !fundingFor) return null;
		const invested = openAction === 'model_withdrawal';
		const label = invested ? 'invested in the model' : 'cash';
		const available = invested ? fundingFor.availableInvested : fundingFor.availableCash;
		const committed = invested ? fundingFor.committedInvested : fundingFor.committedCash;
		const actionCurrency = withdrawalAccount?.currency || baseCurrency;
		const parts = [`${formatMoney(available, actionCurrency)} ${label} available`];
		if (committed > 0)
			parts.push(`${formatMoney(committed, actionCurrency)} committed to pending instructions`);
		if (fundingFor.asOf) parts.push(`as at ${fundingFor.asOf}`);
		return parts.join(' · ');
	});
</script>

<!-- The panel-level edit control. One definition, two panels: details and bank details each carry
     their own, because they are separate saves against separate endpoints and separate permission
     families (`contacts:write` edits the person, `instructions:write` owns where their money is
     paid). The icon TOGGLES, so the way out of a form is where the way in was. -->
{#snippet noAccountMatches()}
	<!-- The filter miss stated the fact and stopped there, while the Clear that undoes it sits on
	     the toolbar above — findable, but the reader has to look away from the answer to find it.
	     Same treatment as the accounts and transactions books, so the narrowed state reads the
	     same whether you are looking at one client's accounts or the firm's four hundred. -->
	<EmptyState
		icon={LandmarkIcon}
		title="No accounts match your filters"
		description="Try a different status or product, or clear the filters to see all {detail.accounts
			.length} of this client’s accounts."
		action={clearAccountMatches}
		compact
		chromeless
	/>
{/snippet}

{#snippet clearAccountMatches()}
	<Button
		variant="outline"
		size="sm"
		onclick={() => {
			accountQuery = '';
			clearAccountFilters();
		}}>Clear filters</Button
	>
{/snippet}

{#snippet editPencil(editing: boolean, toggle: () => void, label: string)}
	<Button
		variant="ghost"
		size="icon-sm"
		class={editing ? undefined : 'text-muted-foreground'}
		onclick={toggle}
		aria-pressed={editing}
		title={editing ? `Stop editing ${label}` : `Edit ${label}`}
	>
		{#if editing}
			<XIcon class="size-4" />
		{:else}
			<PencilIcon class="size-4" />
		{/if}
		<span class="sr-only">{editing ? `Stop editing ${label}` : `Edit ${label}`}</span>
	</Button>
{/snippet}

<!-- ONE SECTION OF THE CLIENT RECORD: a card, a pencil, and either the register or the form.
     `body` receives `editing` so a caller renders its own two branches, because the FIELDS differ
     between them (a picker is a name in view mode and a select in edit mode) even though the
     chrome does not. The pencil is hidden entirely without the matching action (`updateClient`
     for the person cards, `updateBankAccount` for bank) — a read-only mount gets the register
     and no affordance. -->
{#snippet sectionCard(
	key: string,
	title: string,
	body: Snippet<[boolean]>,
	subtitle: string | null = null,
	editable = true,
	editKind: 'client' | 'bank' = 'client',
	holds: ReviewRow[] = []
)}
	<!-- A change to this card that is waiting for a second person is said HERE: the banner under
	     the title names the case, who raised it and whose move it is, and the register is
	     followed by what was proposed, now vs proposed, so the card reads as modified rather
	     than as unchanged with its change somewhere else. -->
	<Card glass size="sm" class="gap-2 py-3">
		<CardHeader class="flex flex-row items-start justify-between gap-3 pb-0">
			<div class="min-w-0">
				<CardTitle class="text-base">{title}</CardTitle>
				{#if subtitle}
					<p class="text-muted-foreground mt-0.5 text-xs">{subtitle}</p>
				{/if}
			</div>
			{#if editable && canEditSection(editKind)}
				{@render editPencil(
					isEditing(key),
					() => (editingSection = isEditing(key) ? null : key),
					title.toLowerCase()
				)}
			{/if}
		</CardHeader>
		<CardContent>
			{#each holds as hold (hold.uid)}
				<ReviewHoldBanner
					review={hold}
					href={links.review?.(hold.uid) ?? null}
					cancelAction={hold.is_mine ? (actions?.cancelReview ?? null) : null}
					enhance={hold.is_mine && actions?.cancelReview ? enhanceForm : null}
					class="mb-3"
				/>
			{/each}
			{@render body(editable && canEditSection(editKind) && isEditing(key))}
			{#if holds.length && !isEditing(key)}
				{#each holds as hold (hold.uid)}
					<div class="border-border/60 mt-3 border-t pt-3">
						<p class="text-muted-foreground mb-1.5 text-xs">
							Proposed in {reviewCaseRef(hold.case_no)} — now and proposed
						</p>
						<ReviewChange review={hold} showClient={false} showAccount={false} showTitle={false} />
					</div>
				{/each}
			{/if}
		</CardContent>
	</Card>
{/snippet}

<!-- The register. EVERY field, always — an em dash for the ones nobody has answered, because a
     section a firm is meant to complete has to show its own gaps. The old view dropped empty rows,
     which is why nobody noticed `relevant_uk_earnings` had no editor for five migrations. -->
{#snippet register(items: Row[], layout: 'wide' | 'narrow' | 'thirds' = 'wide')}
	<!-- THE NARROW LAYOUT WRAPS; THE OTHER TWO CLIP. A `narrow` register is the 22rem rail — one
	     column, a long label ("Country of birth") beside its value in about 200px — and a rail is
	     the one place where the value CANNOT be read anywhere else, so truncating it there hides
	     the field's whole content behind a hover ("Investment Income or Sale of Investments" is a
	     real option and clips to three words). A `wide`/`thirds` register sits in a multi-column
	     card where a wrapped value would ragged the columns, and the row is wide enough that
	     clipping is the exception — so those keep `TruncatedText`.
	     This restores the `wrap` behaviour 851740dc dropped when it moved the compliance card's
	     source of wealth behind a pencil. -->
	{@const wrap = layout === 'narrow'}
	<dl
		class={layout === 'narrow'
			? 'grid min-w-0 text-xs'
			: layout === 'thirds'
				? 'grid min-w-0 grid-cols-3 gap-x-8 text-xs'
				: 'grid min-w-0 gap-x-8 text-xs sm:grid-cols-2 lg:grid-cols-3'}
	>
		{#each items as row (row.label)}
			<!-- A GRID ROW, NOT A FLEX ONE. A flex `shrink-0` label holds its max-content width
			     whatever the card can spare, so in the rail the LONGEST LABEL decides how little
			     room every value gets — measured in Chrome at a 22rem card, "Country of birth"
			     leaves 232px and its own value clips as soon as the rail narrows, while "Source of
			     wealth" clips at every width. `minmax(min-content, auto)` gives the label its
			     natural width when there is room and lets it yield when there is not, with a floor
			     that keeps it inside its own cell; the value track is `minmax(0, 1fr)`, so it takes
			     the rest and can never be the thing that widens the row. -->
			<div
				class="border-border/50 grid min-w-0 grid-cols-[minmax(min-content,auto)_minmax(0,1fr)] items-baseline gap-3 border-b py-1.5"
			>
				<dt class="text-muted-foreground text-xs">{row.label}</dt>
				<dd
					class="min-w-0 text-right {wrap ? 'break-words' : 'overflow-hidden'} {row.value
						? ''
						: 'text-muted-foreground/60'}"
				>
					{#if row.lines?.length}
						<!-- A list-valued fact stacks under its ONE label, a line each, rather than
						     becoming several rows of the register (which keys by label). Always
						     truncated: a stacked line has the same clipping problem a single one
						     does, and `wrap` is about one long value, not a list of short ones.

						     THE STACK IS ONE BLOCK, right-aligned as a whole, with its lines
						     left-aligned INSIDE it. Right-aligning each line independently ragged
						     their left edges, so `Home:` on one row sat indented from `Home:` on the
						     next — the reader's eye has to find the label before the number, and it
						     could not run down a straight edge to do it. -->
						<div class="inline-flex max-w-full flex-col items-start gap-0.5 text-left">
							{#each row.lines as line (line)}
								<TruncatedText text={line} class="max-w-full text-left" />
							{/each}
						</div>
					{:else if row.money}
						<Money amount={row.money.amount} currency={row.money.currency} />
					{:else if !row.value}
						—
					{:else if row.href}
						<a href={row.href} class="font-medium {LINK_RECORD}">{row.value}</a>
					{:else if row.label === 'Bank'}
						<span class="inline-flex max-w-full items-center justify-end gap-1.5">
							<BankMark name={row.value} bic={null} />
							{#if wrap}
								<span class="min-w-0">{row.value}</span>
							{:else}
								<TruncatedText text={row.value} class="min-w-0 text-right" />
							{/if}
						</span>
					{:else if wrap}
						{row.value}
					{:else}
						<!-- `TruncatedText` rather than a native `title`: the tooltip is keyboard-reachable
						     and only appears when the box is actually clipping. -->
						<TruncatedText text={row.value} class="text-right" />
					{/if}
				</dd>
			</div>
		{/each}
	</dl>
{/snippet}

<!-- Save / Cancel, bottom right. One definition so five forms cannot disagree about which button
     is primary or where it sits — and `type="submit"` on the first means Enter in any field of any
     section saves that section, which is the behaviour a one-form record had for free. -->
{#snippet saveBar()}
	<div class="flex items-center justify-end gap-2 pt-1 col-span-full">
		<Button type="button" variant="ghost" disabled={saving} onclick={() => (editingSection = null)}>
			Cancel
		</Button>
		<Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
	</div>
{/snippet}

<!-- ONBOARDING'S ADDRESS: country + Postcoder on one row, then a summary of what was chosen
     (Edit / Clear), with the line fields only when someone asks to type. The record used to
     stack the lookup on top of five always-visible boxes, which read as a broken autocomplete
     rather than the same control the fact-find uses. Hidden inputs keep the POST shape
     (`addr_line1` …) when the parts are not on screen. -->
{#snippet addressEditor(p: {
	lookupId: string;
	prefix: string;
	pick: Address | null;
	setPick: (v: Address | null) => void;
	stored: unknown;
	country: string;
	setCountry: (v: string) => void;
	editing: boolean;
	setEditing: (v: boolean) => void;
})}
	{@const shown = addressHasContent(p.pick, p.stored)}
	{@const summary = p.pick ? formatPickedAddress(p.pick) : formatStoredAddress(p.stored)}
	<div class="space-y-3 col-span-full">
		<div class="grid gap-2 sm:grid-cols-[minmax(10rem,1fr)_minmax(14rem,2fr)]">
			<div class="space-y-1">
				<Label for={`${p.lookupId}_country`} class="text-muted-foreground text-xs">Country</Label>
				<Combobox
					id={`${p.lookupId}_country`}
					name={`${p.prefix}_country`}
					bind:value={() => p.country, (v) => p.setCountry(v || 'GB')}
					options={countryOptions}
					placeholder="Select a country…"
					onOpen={() => void ensureCountries()}
				/>
			</div>
			<div class="space-y-1">
				<Label for={p.lookupId} class="text-muted-foreground text-xs">Find address</Label>
				{#key p.country}
					<AddressLookup
						id={p.lookupId}
						country={p.country}
						placeholder={p.country === 'GB'
							? 'Start typing an address or postcode…'
							: 'Start typing an address, city or postal code…'}
						bind:value={
							() => p.pick,
							(v) => {
								p.setPick(v);
								if (v) {
									p.setEditing(false);
									if (v.countryCode) p.setCountry(v.countryCode);
								}
							}
						}
					/>
				{/key}
			</div>
		</div>
		{#if !shown}
			<div class="flex justify-end">
				<Button type="button" variant="ghost" size="sm" onclick={() => p.setEditing(true)}>
					Enter address manually
				</Button>
			</div>
		{:else}
			<div
				class="border-input bg-muted/40 flex items-start justify-between gap-3 rounded-lg border px-2.5 py-2 text-sm"
			>
				<p class="min-w-0 flex-1">{summary}</p>
				<div class="flex shrink-0 items-center gap-1">
					<Button type="button" variant="ghost" size="sm" onclick={() => p.setEditing(!p.editing)}>
						{p.editing ? 'Done' : 'Edit'}
					</Button>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onclick={() => {
							p.setPick({
								line1: '',
								town: '',
								postcode: '',
								country: '',
								countryCode: p.country
							});
							p.setEditing(false);
						}}
					>
						Clear
					</Button>
				</div>
			</div>
		{/if}
		{#if p.editing}
			<div class="grid gap-2 sm:grid-cols-2">
				<div class="space-y-1 sm:col-span-2">
					<Label for={`${p.prefix}_line1`} class="text-muted-foreground text-xs">Line 1</Label>
					<Input
						id={`${p.prefix}_line1`}
						name={`${p.prefix}_line1`}
						value={fieldFrom(p.pick, p.stored, 'line1')}
					/>
				</div>
				<div class="space-y-1 sm:col-span-2">
					<Label for={`${p.prefix}_line2`} class="text-muted-foreground text-xs">
						Line 2 <span class="font-normal">(optional)</span>
					</Label>
					<Input
						id={`${p.prefix}_line2`}
						name={`${p.prefix}_line2`}
						value={fieldFrom(p.pick, p.stored, 'line2')}
					/>
				</div>
				<div class="space-y-1">
					<Label for={`${p.prefix}_city`} class="text-muted-foreground text-xs">City / town</Label>
					<Input
						id={`${p.prefix}_city`}
						name={`${p.prefix}_city`}
						value={fieldFrom(p.pick, p.stored, 'city')}
					/>
				</div>
				<div class="space-y-1">
					<Label for={`${p.prefix}_postcode`} class="text-muted-foreground text-xs">Postcode</Label>
					<Input
						id={`${p.prefix}_postcode`}
						name={`${p.prefix}_postcode`}
						value={fieldFrom(p.pick, p.stored, 'postcode')}
					/>
				</div>
			</div>
		{:else}
			<input
				type="hidden"
				name={`${p.prefix}_line1`}
				value={fieldFrom(p.pick, p.stored, 'line1')}
			/>
			<input
				type="hidden"
				name={`${p.prefix}_line2`}
				value={fieldFrom(p.pick, p.stored, 'line2')}
			/>
			<input type="hidden" name={`${p.prefix}_city`} value={fieldFrom(p.pick, p.stored, 'city')} />
			<input
				type="hidden"
				name={`${p.prefix}_postcode`}
				value={fieldFrom(p.pick, p.stored, 'postcode')}
			/>
		{/if}
	</div>
{/snippet}

<!-- Two flex columns, not a grid. CSS grid rows share height across columns, so a tall
     main column opened a gap between Compliance and the rail even with items-start — the
     ROW was still as tall as main's share of it. Flex columns size independently.
     `contents` on the wrappers below lg flattens them so the five blocks are one column,
     ordered by `order-*` (identity, advisers, compliance, main, rail). lg+ is two columns
     (md is too early inside the platform sidebar). -->
<div class="flex flex-col gap-6 lg:flex-row lg:items-start">
	{#snippet complianceBody(editing: boolean)}
		<!-- THE TICKS ARE READ-ONLY. They are written by the screening provider and the flow
		     engine; an adviser ticking "AML done" by hand would put an unevidenced clearance
		     on a regulated record. Source of wealth is the exception: it is Morgan Lloyd's
		     closed list, and a typed "Investments" filled our column and left theirs empty.
		     Occupation moved to Personal — it is who they are, not a compliance tick.
		     The person-facts (US person, source of wealth, place of birth) are INDIVIDUALS
		     only: none of them is a fact about a trust or a company. -->
		<div class="space-y-1">
			{#each COMPLIANCE_CHECKS as { kind, title } (kind)}
				{@const check = checkFor(kind)}
				<div class="flex items-center justify-between gap-3 py-1 text-xs">
					<span class="flex items-center gap-2">
						{#if check?.status === 'passed'}
							<CheckIcon class="text-success-strong size-4 shrink-0" />
						{:else if check?.status === 'failed'}
							<XIcon class="text-destructive-strong size-4 shrink-0" />
						{:else}
							<!-- Not a cross: "nobody has recorded this" is not a failure, and a red mark
								     against an unstarted check reads as one. -->
							<CircleDashedIcon class="text-muted-foreground/50 size-4 shrink-0" />
						{/if}
						<span>{title}</span>
					</span>
					<span class="text-muted-foreground text-xs">
						{#if check?.status === 'passed' && check.verified_at}
							{formatDate(check.verified_at)}
						{:else if check?.status}
							{label(check.status)}
						{:else}
							Not recorded
						{/if}
					</span>
				</div>
			{/each}
		</div>
		{#if (detail.term_acceptances ?? []).length}
			<div class="mt-3 space-y-1 border-t pt-3">
				{#each detail.term_acceptances ?? [] as acceptance (acceptance.uid)}
					<div class="flex items-baseline justify-between gap-3 py-0.5 text-xs">
						<span>
							{acceptance.title}
							<span class="text-muted-foreground">v{acceptance.version_number}</span>
						</span>
						<span class="text-muted-foreground">
							{formatDate(acceptance.accepted_at)}
							{#if acceptance.channel === 'in_person'}
								· in person
								<!-- The signed copy is what makes an in-person acceptance more than the adviser's
								     word for it, so it is shown beside the acceptance, not only on the journey. -->
								{#if acceptance.evidence_document_uid && links.document}
									·
									<a
										href={links.document(acceptance.evidence_document_uid)}
										class="font-medium {LINK_RECORD}"
									>
										Signed copy
									</a>
								{:else if acceptance.evidence_document_uid}
									· signed copy on file
								{/if}
							{/if}
						</span>
					</div>
				{/each}
			</div>
		{/if}

		{#if registeredContactRelationships.length}
			<Separator class="my-3" />
			<div class="space-y-2 text-xs">
				{#each registeredContactRelationships as relationship (relationship.id)}
					<div class="flex items-baseline justify-between gap-3">
						<span class="text-muted-foreground">
							{relationship.outbound ? 'Registered contact of' : 'Registered contact'}
						</span>
						{#if links.client}
							<a
								href={links.client(relationship.other_uid)}
								class="text-right font-medium {LINK_RECORD}"
							>
								{relationshipName(relationship)}
							</a>
						{:else}
							<span class="text-right font-medium">{relationshipName(relationship)}</span>
						{/if}
					</div>
				{/each}
			</div>
		{/if}

		{#if !isOrg}
			<Separator class="my-3" />

			<!-- Source of funds, source of wealth, and the US-person answer are the controls while
			     the form is open, and the register when it is closed. -->
			{@const facts = !editing ? complianceFacts : []}

			{#if editing}
				<!-- THE REGISTER GOES INSIDE THE FORM, ABOVE THE SAVE BAR. Compliance is the one
				     section that shows a form AND a register at once — everywhere else the form
				     REPLACES the register, because everything in the section is editable. Here the
				     ticks and the US-person declaration nobody edits from this card have to stay on
				     screen, and rendering them after the form put those facts BELOW Save/Cancel,
				     where they read as having fallen out of the panel. Buttons end a panel; nothing
				     may sit under them. A `<dl>` is valid form content, and the rows post nothing.
				     Multipart because the signed W-9 or W-8 rides this Save. -->
				<form
					method="POST"
					enctype="multipart/form-data"
					action={actions?.updateClient}
					use:enhanceForm={withToast(savedOrHeld('Compliance details updated'), {
						pending: (v) => (saving = v),
						reset: false,
						onSuccess: () => (editingSection = null)
					})}
					class="min-w-0 space-y-3"
				>
					<input type="hidden" name="type" value={c.type} />
					<div class="min-w-0 space-y-1">
						<Label for="us_person">Is a US person?</Label>
						<!-- What the record held, so a blank answer is still checked against it. -->
						<input
							type="hidden"
							name="us_person_was"
							value={c.us_person == null ? '' : c.us_person ? 'Yes' : 'No'}
						/>
						<SelectMenu
							id="us_person"
							name="us_person"
							items={[{ value: '', label: '—' }, ...usPersonChoices]}
							bind:value={usPersonDraft}
						/>
					</div>
					<div class="min-w-0 space-y-1">
						<Label for="fatca_status">Does this client have a W-9 or a W-8?</Label>
						<!-- What the record held, so a save that leaves it alone keeps its date. -->
						<input type="hidden" name="fatca_status_was" value={c.fatca_status ?? ''} />
						<SelectMenu
							id="fatca_status"
							name="fatca_status"
							items={[{ value: '', label: '—' }, ...fatcaStatusChoices]}
							bind:value={fatcaStatusDraft}
						/>
					</div>
					<FatcaDocumentField
						status={fatcaStatusDraft}
						savedStatus={c.fatca_status}
						document={c.fatca_document}
						href={links.document}
					/>
					<div class="min-w-0 space-y-1">
						<Label for="source_of_funds">Source of funds</Label>
						<SelectMenu
							id="source_of_funds"
							name="source_of_funds"
							items={[{ value: '', label: '—' }, ...sourceOfFundsChoices]}
							bind:value={sourceOfFundsDraft}
						/>
					</div>
					{#if sourceOfFundsDraft === 'Other (please specify)'}
						<div class="space-y-1">
							<Label for="source_of_funds_detail">Source of funds (other)</Label>
							<Input
								id="source_of_funds_detail"
								name="source_of_funds_detail"
								bind:value={sourceOfFundsDetailDraft}
							/>
						</div>
					{/if}
					<div class="min-w-0 space-y-1">
						<Label for="source_of_wealth">Source of wealth</Label>
						<SelectMenu
							id="source_of_wealth"
							name="source_of_wealth"
							items={[{ value: '', label: '—' }, ...sourceOfWealthChoices]}
							bind:value={sourceOfWealthDraft}
						/>
					</div>
					<div class="space-y-1">
						<Label for="source_of_wealth_detail">Source of wealth statement</Label>
						<Textarea
							id="source_of_wealth_detail"
							name="source_of_wealth_detail"
							bind:value={sourceOfWealthDetailDraft}
							minlength={30}
							rows={3}
							required
							placeholder="Describe the source in at least 30 characters"
						/>
					</div>
					{#if facts.length}
						{@render register(facts, 'narrow')}
					{/if}
					{@render saveBar()}
				</form>
			{:else}
				{@render register(facts, 'narrow')}
			{/if}
		{/if}
	{/snippet}

	<div class="contents lg:flex lg:min-w-0 lg:flex-1 lg:flex-col lg:gap-6">
		<Card glass={!vulnerable} class={cn('order-1 min-w-0', vulnerable && VULNERABLE_CARD_CLASS)}>
			{#if vulnerable}
				<VulnerabilityBanner
					reasons={vulnReasons}
					characteristics={c.vulnerability_characteristics ?? []}
					isTemporary={vulnTemporary}
					recordedAt={vulnRecorded}
					notes={c.vulnerability_notes}
					onedit={actions?.setVulnerability ? editVulnerability : undefined}
				/>
			{/if}
			{#if pendingVulnerability}
				<!-- The vulnerability record is MODIFIED and waiting: the header used to say nothing
				     until the change applied and the card turned red. The same banner the card
				     below carries, up here where the flag is read on every page. -->
				<div class="px-6 pt-5">
					<ReviewHoldBanner
						review={pendingVulnerability}
						what="Vulnerability record"
						href={links.review?.(pendingVulnerability.uid) ?? null}
					/>
				</div>
			{/if}
			<CardContent>
				<!-- `RecordHero` — the same component the ACCOUNT record's head is. The row, the disc,
				     the name, the badge row, the facts row and the actions are one definition, so the two
				     records cannot drift on the weight of a name or the gap between two facts; they had
				     already drifted on both. The overflow menu is IN THAT ROW, at its end, never
				     absolutely positioned into the card's corner — which is where the vulnerability
				     banner's own "Update" button lives, and where the kebab used to cover it. -->
				<RecordHero {name} kind={clientKind(c)}>
					{#snippet badges()}
						<!-- The SPECIFIC kind: a trust badged "Organisation" is the same loss of detail the
							     avatar's building glyph already has, and this is the one place a reader looks to
							     find out what they are dealing with. Falls back to the base type for an
							     organisation whose kind nobody has recorded. -->
						<Badge variant="outline"
							>{clientTypeLabel(c.sub_type ?? c.type) ?? humanise(c.type)}</Badge
						>
						{#if c.service_type}
							<Badge variant="secondary">
								{serviceLabel(c.service_type)}
							</Badge>
						{/if}
						{#if resolvedClassification || classifications.length}
							{#if canPinClassification}
								<DropdownMenu.Root>
									<DropdownMenu.Trigger
										class="focus-visible:ring-ring/50 inline-flex shrink-0 cursor-pointer rounded-full outline-none focus-visible:ring-2"
										title="Client book"
										aria-label="Client book"
									>
										<Badge variant="outline">
											{resolvedClassification?.name ?? 'Unassigned book'}
											{#if resolvedClassification?.source === 'pinned'}
												<span class="text-muted-foreground font-normal">pinned</span>
											{/if}
										</Badge>
									</DropdownMenu.Trigger>
									<DropdownMenu.Content align="start" class="w-56">
										<DropdownMenu.Item onclick={() => void pinBook('')}>
											Match automatically
										</DropdownMenu.Item>
										{#each classifications as book (book.uid)}
											<DropdownMenu.Item onclick={() => void pinBook(book.uid)}>
												{book.name}
											</DropdownMenu.Item>
										{/each}
									</DropdownMenu.Content>
								</DropdownMenu.Root>
							{:else if resolvedClassification}
								<Badge variant="outline">{resolvedClassification.name}</Badge>
							{/if}
						{/if}
						{#each assignedGroups as group (group.uid)}
							<Badge variant="secondary">{group.name}</Badge>
						{/each}
						{#if canSetGroups}
							<DropdownMenu.Root>
								<DropdownMenu.Trigger
									class="focus-visible:ring-ring/50 inline-flex shrink-0 cursor-pointer rounded-full outline-none focus-visible:ring-2"
									title="Client groups"
									aria-label="Client groups"
								>
									<Badge variant="outline">
										{assignedGroups.length ? 'Groups' : 'Add to a group'}
									</Badge>
								</DropdownMenu.Trigger>
								<DropdownMenu.Content align="start" class="w-56">
									{#each groups as group (group.uid)}
										<DropdownMenu.CheckboxItem
											checked={assignedGroups.some((assigned) => assigned.uid === group.uid)}
											onCheckedChange={() => void toggleGroup(group.uid)}
										>
											{group.name}
										</DropdownMenu.CheckboxItem>
									{/each}
								</DropdownMenu.Content>
							</DropdownMenu.Root>
						{/if}
						<StatusBadge tone={statusTone(recordStatus)}>{humanise(recordStatus)}</StatusBadge>
						<!-- Whether this client can SIGN IN, and as whom, is a property of the person rather
							     than of their money — so it hangs off the name. A badge, not a lone key: "no
							     logins" and "they can sign in" have to read as different states at a glance, and
							     an icon cannot say which.
							     Rendered only when a loader has supplied `users`: absent is not empty, and a
							     mount that never asked must not answer "no logins linked".
							     No portal access opens an action menu (Invite, and Manage profiles); portal
							     access still opens the profiles dialog. -->
						{#if detail.users}
							{#if hasPortalAccess}
								<button
									type="button"
									onclick={() => {
										openLogins = true;
										void ensureMembers();
									}}
									class="focus-visible:ring-ring/50 inline-flex shrink-0 cursor-pointer rounded-full outline-none focus-visible:ring-2"
									title="Client profiles"
									aria-label="Client profiles"
								>
									<StatusBadge tone="success">
										<KeyRoundIcon class="size-3" />
										Portal access
									</StatusBadge>
								</button>
							{:else}
								<DropdownMenu.Root>
									<DropdownMenu.Trigger
										class="focus-visible:ring-ring/50 inline-flex shrink-0 cursor-pointer rounded-full outline-none focus-visible:ring-2"
										title="Portal access"
										aria-label="Portal access"
									>
										<StatusBadge tone="neutral">
											<KeyRoundIcon class="size-3" />
											No portal access
										</StatusBadge>
									</DropdownMenu.Trigger>
									<DropdownMenu.Content align="start" class="w-48">
										{#if canInvitePortal}
											<DropdownMenu.Item
												disabled={inviting}
												onclick={() => inviteForm?.requestSubmit()}
											>
												Invite to portal
											</DropdownMenu.Item>
										{/if}
										<DropdownMenu.Item
											onclick={() => {
												openLogins = true;
												void ensureMembers();
											}}
										>
											Manage profiles
										</DropdownMenu.Item>
									</DropdownMenu.Content>
								</DropdownMenu.Root>
							{/if}
						{/if}
					{/snippet}
					<!-- Email, mobile and address under the name, each an icon and a clause. Hover (or
					     focus) reveals a copy icon; click copies and toasts. An EMAIL has no break
					     opportunity at all — not `@`, not `.` — so `max-w-full` + `break-words` lets a long
					     address wrap inside its own item. The adviser is no longer named here: the card
					     beside this one is about exactly that. -->
					{#snippet facts()}
						<!-- The client's reference, first in the row: what an adviser reads out on a call
							     and what a custodian asks for first, ahead of email/mobile/address. -->
						{#if reference}
							<CopyButton
								value={reference}
								label="Copy client reference"
								reveal="hover"
								class={HEADER_COPY_CLASS}
								oncopy={() => toast.success('Client reference copied')}
							>
								<HashIcon class="size-3 shrink-0" />
								<span class="min-w-0 font-mono break-words">{reference}</span>
							</CopyButton>
						{/if}
						{#if c.email}
							<CopyButton
								value={c.email}
								label="Copy email"
								reveal="hover"
								class={HEADER_COPY_CLASS}
								oncopy={() => toast.success('Email copied')}
							>
								<MailIcon class="size-3 shrink-0" />
								<span class="min-w-0 break-words {WRAP_NAME}">{c.email}</span>
							</CopyButton>
						{:else}
							<p class="flex max-w-full items-center gap-1.5 pe-5">
								<MailIcon class="size-3 shrink-0" />
								<span>No email</span>
							</p>
						{/if}
						{#if !isOrg}
							{#if c.mobile}
								<CopyButton
									value={c.mobile}
									label="Copy mobile"
									reveal="hover"
									class={HEADER_COPY_CLASS}
									oncopy={() => toast.success('Mobile copied')}
								>
									<SmartphoneIcon class="size-3 shrink-0" />
									<span class="min-w-0 break-words">{phoneText(c.mobile)}</span>
								</CopyButton>
							{:else}
								<p class="flex max-w-full items-center gap-1.5 pe-5">
									<SmartphoneIcon class="size-3 shrink-0" />
									<span>No mobile</span>
								</p>
							{/if}
						{/if}
						{#if c.phone}
							<CopyButton
								value={c.phone}
								label="Copy phone"
								reveal="hover"
								class={HEADER_COPY_CLASS}
								oncopy={() => toast.success('Phone copied')}
							>
								<PhoneIcon class="size-3 shrink-0" />
								<span class="min-w-0 break-words">{phoneText(c.phone)}</span>
							</CopyButton>
						{/if}
						{#if addressLine}
							<CopyButton
								value={addressLine}
								label="Copy address"
								reveal="hover"
								class="{HEADER_COPY_CLASS} items-start"
								oncopy={() => toast.success('Address copied')}
							>
								<MapPinIcon class="mt-px size-3 shrink-0" />
								<span class="min-w-0 break-words">{addressLine}</span>
							</CopyButton>
						{:else}
							<p class="flex max-w-full items-start gap-1.5 pe-5">
								<MapPinIcon class="mt-px size-3 shrink-0" />
								<span>No address</span>
							</p>
						{/if}
					{/snippet}
					<!-- The header's trailing controls: the one routine action this record hosts, then the
					     overflow menu for the rare ones. Rare, deliberate, recoverable — an overflow menu
					     rather than a row of buttons, which made them look like part of the daily work. The
					     component puts them at the row's trailing edge and keeps them whole when a long
					     name is what has to give. -->
					<!-- The snippet is named for the slot it fills, so inside it `actions` is the SNIPPET
					     and not this component's `actions` PROP — the record's form handlers. `record`
					     below is that prop, aliased in the script for exactly this shadow. -->
					{#snippet actions()}
						{#if canIllustrate}
							<!-- Named for the wrapper the client holds, so an adviser reads "SIPP illustration"
								     rather than a generic word — and "Pension" for a prospect who holds nothing,
								     which is a client this button is very much for. -->
							<Button
								variant="outline"
								size="sm"
								onclick={illustrateAction ?? undefined}
								href={illustrationHref ?? undefined}
								data-action="illustrate"
							>
								<FileChartLineIcon class="text-brand mr-1.5 size-4" />
								{pensionLabel} illustration
							</Button>
						{/if}
						{#if viewAsAction && hasPortalAccess}
							<Button variant="outline" size="sm" onclick={viewAsAction} data-action="view-as">
								<EyeIcon class="text-brand mr-1.5 size-4" />
								View their portal
							</Button>
						{/if}
						{#if overflowActions}
							<!-- The same outline "Actions" control the account record wears, so the two
							     records read the same way: the word says what the three dots did not. -->
							<DropdownMenu.Root>
								<DropdownMenu.Trigger>
									{#snippet child({ props })}
										<Button variant="outline" size="sm" aria-label="Client actions" {...props}>
											<EllipsisIcon class="size-4" />
											Actions
										</Button>
									{/snippet}
								</DropdownMenu.Trigger>
								<DropdownMenu.Content align="end" class="w-56">
									{#if newJourneys.length}
										<DropdownMenu.Group>
											<DropdownMenu.GroupHeading>New journey</DropdownMenu.GroupHeading>
											{#each newJourneys as flow (flow.code)}
												<DropdownMenu.Item data-action="new-journey-{flow.code}">
													{#snippet child({ props })}
														<a {...props} href={flow.href}>
															<RouteIcon class="text-brand size-4" />
															{flow.name}
														</a>
													{/snippet}
												</DropdownMenu.Item>
											{/each}
										</DropdownMenu.Group>
									{/if}
									{#if newJourneys.length && (record?.setVulnerability || canInvitePortal || (record?.restoreClient && closed))}
										<DropdownMenu.Separator />
									{/if}
									{#if record?.setVulnerability && !vulnerable}
										<DropdownMenu.Item onclick={editVulnerability}>
											<ShieldAlertIcon class="text-brand size-4" />
											Record vulnerability
										</DropdownMenu.Item>
									{/if}
									{#if canInvitePortal}
										<DropdownMenu.Item
											disabled={inviting}
											onclick={() => inviteForm?.requestSubmit()}
										>
											<KeyRoundIcon class="text-brand size-4" />
											Invite to portal
										</DropdownMenu.Item>
									{/if}
									{#if record?.restoreClient && closed}
										<DropdownMenu.Item onclick={() => restoreForm?.requestSubmit()}>
											<RotateCcwIcon class="text-brand size-4" />
											Reopen
										</DropdownMenu.Item>
									{/if}
									{#if record?.closeClient && closeable}
										<DropdownMenu.Separator />
										<DropdownMenu.Item
											variant="destructive"
											disabled={closing}
											onclick={() => (closeOpen = true)}
										>
											<ArchiveIcon class="size-4" />
											Close
										</DropdownMenu.Item>
									{/if}
								</DropdownMenu.Content>
							</DropdownMenu.Root>
						{/if}
					{/snippet}
				</RecordHero>
			</CardContent>
		</Card>

		<!-- Main column: figures, then details, then accounts. Details first because the
		     record is opened to read about a person more often than to audit their accounts, and the
		     accounts table is the long thing — a reader scrolling past it to reach an address was
		     scrolling past the answer. -->
		<div class="order-4 min-w-0 space-y-6">
			{#if showOverviewMoney}
				<!-- The app's work queue sits INSIDE the main column rather than spanning the record: it is a
			     prompt about this client's money, which is what this column is, and a full-width strip
			     pushed the rail's first card down a band for no reason. -->
				<ClientActiveJourneys {journeys} href={links.journey} />
				{@render queue?.()}

				<!-- FOUR FIGURES, above the detail. What the client holds, what is liquid, what they paid and
			     what that has become — read together, which is why they are one row of equal cards rather
			     than three inline Stats in the header and nothing about cost anywhere.
			     Book cost and unrealised gain are `null` until a loader supplies them and show an em dash
			     until then: a cost basis cannot be derived from account values, and a zero would read as
			     "bought for nothing", i.e. all gain. -->
				<div class="grid grid-cols-2 gap-4 lg:grid-cols-4">
					<Card glass>
						<CardContent>
							<Stat label="Portfolio value" value={formatMoney(aum, baseCurrency)} />
						</CardContent>
					</Card>
					<Card glass>
						<CardContent>
							<Stat label="Cash" value={formatMoney(cash, baseCurrency)} />
						</CardContent>
					</Card>
					<Card glass>
						<CardContent>
							<Stat
								label="Book cost"
								value={bookCost == null ? '—' : formatMoney(bookCost, baseCurrency)}
							/>
						</CardContent>
					</Card>
					<Card glass>
						<CardContent>
							<!-- The one directional figure here: a gain and a loss are different news, and this is
						     the card read for which of the two it is. -->
							<Stat
								label="Unrealised gain / loss"
								value={unrealisedGain == null
									? '—'
									: formatSignedMoney(unrealisedGain, baseCurrency)}
								valueTone={unrealisedGain == null || unrealisedGain === 0
									? undefined
									: unrealisedGain > 0
										? 'positive'
										: 'negative'}
							/>
						</CardContent>
					</Card>
				</div>
			{/if}

			<!-- Contact, bank, personal, pension and employer. Personal also stays on Overview so opening
			     the record still says who the person is. Each person card saves through the SAME
			     `updateClient` action, carrying only its own fields: the API's PATCH treats a present
			     null as "clear" and an absent key as "leave alone". Bank is the money family
			     (`updateBankAccount`). Compliance ticks stay register-only; source of wealth is
			     the closed list the vendor's INT mapping hits. -->
			<section id="details" class="space-y-6">
				{#if showDetailCards}
					{#snippet contactBody(editing: boolean)}
						{#if editing}
							<form
								method="POST"
								action={actions?.updateClient}
								use:enhanceForm={withToast(savedOrHeld('Contact details updated'), {
									pending: (v) => (saving = v),
									reset: false,
									onSuccess: () => (editingSection = null)
								})}
								class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
							>
								<input type="hidden" name="type" value={c.type} />
								{#if isOrg}
									<div class="space-y-1">
										<!-- Organisations only, because that is what 0002's CHECK allows: an
										     individual has no sub-type and the form must not be able to post one.
										     "—" is a real choice (an organisation of no stated kind), not a
										     placeholder. -->
										<Label for="sub_type">Client type</Label>
										<SelectMenu
											id="sub_type"
											name="sub_type"
											items={SUB_TYPE_ITEMS}
											bind:value={subType}
										/>
									</div>
									<div class="space-y-1 sm:col-span-2">
										<Label for="name">Organisation name</Label>
										<Input id="name" name="name" value={c.name ?? ''} />
									</div>
								{:else}
									<div class="space-y-1">
										<Label for="title">Title</Label>
										<SelectMenu
											id="title"
											name="title"
											items={titleItems(c.title)}
											bind:value={titleDraft}
											placeholder="Select title"
										/>
									</div>
									<div class="space-y-1">
										<Label for="first_name">First name</Label>
										<Input id="first_name" name="first_name" bind:value={firstNameDraft} />
									</div>
									<div class="space-y-1">
										<Label for="surname">Surname</Label>
										<Input id="surname" name="surname" bind:value={surnameDraft} />
									</div>
								{/if}
								<div class="space-y-1">
									<Label for="email">Email</Label>
									<!-- The shared email field, like Mobile and Phone below: it names a bad address
									     as it is typed. The action re-validates, because a client can defeat it. -->
									<EmailInput id="email" name="email" value={c.email ?? ''} />
								</div>
								{#if !isOrg}
									<div class="space-y-1">
										<Label for="mobile">Mobile</Label>
										<PhoneNumberInput
											id="mobile"
											name="mobile"
											value={c.mobile ?? ''}
											onchange={() => {}}
										/>
									</div>
								{/if}
								<!-- Shown for everyone. An organisation has no personal mobile; a switchboard is
								     a phone, and gating this the same way would leave it with no number at all. -->
								<div class="space-y-1">
									<Label for="phone">Phone</Label>
									<PhoneNumberInput
										id="phone"
										name="phone"
										value={c.phone ?? ''}
										onchange={() => {}}
									/>
								</div>
								{@render addressEditor({
									lookupId: 'addr_lookup',
									prefix: 'addr',
									pick: addressPick,
									setPick: (v) => (addressPick = v),
									stored: c.address,
									country: addressCountry,
									setCountry: (v) => (addressCountry = v),
									editing: editingContactAddress,
									setEditing: (v) => (editingContactAddress = v)
								})}
								{@render saveBar()}
							</form>
						{:else}
							{@render register([
								...rows(
									...(isOrg
										? ([
												['Client type', clientTypeLabel(c.sub_type ?? c.type) ?? humanise(c.type)],
												['Organisation name', c.name]
											] as [string, unknown][])
										: ([
												['Title', c.title],
												['First name', c.first_name],
												['Surname', c.surname]
											] as [string, unknown][])),
									['Email', c.email],
									...(isOrg ? [] : ([['Mobile', phoneText(c.mobile)]] as [string, unknown][])),
									...(phoneText(c.phone)
										? ([['Phone', phoneText(c.phone)]] as [string, unknown][])
										: [])
								),
								...rows(['Address', addressLine || null])
							])}
						{/if}
					{/snippet}
					{#each pendingOffCards as review (review.uid)}
						<!-- A detail edit on a field no card owns waits above the cards, as every detail
						     hold once did; one on a card's own fields is said on that card. -->
						<Card glass size="sm" class="gap-2 py-3">
							<CardContent>
								<PendingReview
									{review}
									href={links.review?.(review.uid) ?? null}
									showAccount={false}
									cancelAction={review.is_mine ? (actions?.cancelReview ?? null) : null}
									enhance={review.is_mine && actions?.cancelReview ? enhanceForm : null}
								/>
							</CardContent>
						</Card>
					{/each}
					{@render sectionCard(
						'contact',
						'Contact details',
						contactBody,
						null,
						true,
						'client',
						holdsFor('contact')
					)}

					{#snippet bankBody(editing: boolean)}
						{#if editing}
							<form
								method="POST"
								action={actions?.updateBankAccount}
								use:enhanceForm={onBankSubmit}
								autocomplete="off"
							>
								<BankAccountFields
									bind:scheme={bankSchemeDraft}
									bind:name={bankNameDraft}
									bind:sortCode={bankSortDraft}
									bind:accountNumber={bankNumberDraft}
									bind:iban={bankIbanDraft}
									bind:bic={bankBicDraft}
									bind:method={bankMethodDraft}
									bind:directoryStatus={bankDirectoryStatus}
									bind:directoryResult={bankDirectoryResult}
									errors={bankErrors}
									idPrefix="bank-"
									names={{
										scheme: 'bank_scheme',
										name: 'bank_name',
										sortCode: 'bank_sort_code',
										accountNumber: 'bank_account_number',
										iban: 'bank_iban',
										bic: 'bank_bic',
										method: 'bank_method'
									}}
									onSchemeChange={() => (bankErrors = {})}
								>
									{#snippet footer()}
										<p class="text-muted-foreground col-span-full text-xs">
											Clearing every field removes the details. Accounts with none of their own use
											these.
										</p>
										{#if bankNeedsReview}
											<div class="col-span-full space-y-1">
												<Label for="bank_justification">Why this change</Label>
												<Textarea
													id="bank_justification"
													name="justification"
													bind:value={bankJustification}
													rows={2}
													required
													aria-invalid={bankJustificationError ? 'true' : undefined}
												/>
												{#if bankJustificationError}
													<p class="text-destructive text-xs">{bankJustificationError}</p>
												{:else}
													<p class="text-muted-foreground text-xs">
														A second person must approve before these details change.
													</p>
												{/if}
											</div>
										{/if}
										{@render saveBar()}
									{/snippet}
								</BankAccountFields>
							</form>
						{:else}
							{#if !bankDetailsAllowed}
								<p class="text-muted-foreground mb-3 text-sm">
									Bank details do not apply — none of this client’s products accept withdrawals.
								</p>
							{/if}
							<!-- The register stays while a change waits: the card says what the record holds
							     NOW, the banner above says a change is waiting, and the proposal follows. -->
							{@render register(
								rows(
									...(bank
										? bankDisplayRows(bank)
										: ([
												['Account name', null],
												['Sort code', null],
												['Account number', null]
											] as [string, unknown][]))
								),
								'thirds'
							)}
						{/if}
					{/snippet}
					{@render sectionCard(
						'bank',
						'Bank details',
						bankBody,
						bankDetailsAllowed
							? 'Used by every account this client holds when the account has none of its own.'
							: 'None of this client’s products accept withdrawal bank details.',
						bankDetailsAllowed,
						'bank',
						pendingBank ? [pendingBank] : []
					)}
				{/if}

				<!-- NO VULNERABILITY CARD HERE. Two branches each added one on the same day — this
				     one, ungated, and the `id="vulnerability"` card under `showDetailCards` below —
				     and the merge kept both, so Details stated the flag three times (banner, card,
				     card). The one below is the keeper: it has the anchor the banner's Update
				     scrolls to, the pencil, Duration and Recorded. Overview keeps the banner. -->

				{#if !isOrg}
					{#snippet personalBody(editing: boolean)}
						{#if editing}
							<form
								method="POST"
								action={actions?.updateClient}
								use:enhanceForm={(input) => {
									const message = nationalInsuranceError(
										identifierSpec?.name,
										input.formData.get('national_identifier')
									);
									if (message) {
										nationalIdentifierDraft = String(
											input.formData.get('national_identifier') ?? ''
										);
										input.cancel();
										return;
									}
									return withToast(savedOrHeld('Personal details updated'), {
										pending: (v) => (saving = v),
										reset: false,
										onSuccess: () => (editingSection = null)
									})(input);
								}}
								class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
							>
								<input type="hidden" name="type" value={c.type} />
								<div class="space-y-1">
									<Label for="dob">Date of birth</Label>
									<Input
										id="dob"
										name="dob"
										type="date"
										autocomplete="off"
										class="[&::-webkit-calendar-picker-indicator]:cursor-pointer dark:[&::-webkit-calendar-picker-indicator]:invert"
										bind:value={dobDraft}
									/>
								</div>
								<div class="space-y-1">
									<Label>Gender</Label>
									<!-- Two options, so a button group rather than a dropdown: the whole answer
									     is visible and it is one click (the contribution-type pattern). Segmented
									     renders buttons, so the value travels in a hidden field. Content-width
									     (`inline-flex`) so Male / Female sit in this column rather than stretching
									     the row. -->
									<Segmented
										bind:value={genderDraft}
										options={genderChoices}
										label="Gender"
										shape="square"
									/>
									<input type="hidden" name="gender" value={genderDraft} />
								</div>
								<div class="space-y-1">
									<!-- Occupation is what they DO; employment status (on the pension section)
									     is whether they work. A retired GP is both, and Morgan Lloyd models
									     exactly that split. `allowCustom` because ninety-seven is a long list
									     and still not every job. -->
									<Label for="occupation">Occupation</Label>
									<Combobox
										id="occupation"
										name="occupation"
										bind:value={occupationDraft}
										options={occupationChoices}
										allowCustom
										placeholder="Search occupations…"
									/>
								</div>
								{#if showOccupationDetail}
									<div class="space-y-1 col-span-full">
										<Label for="occupation_detail">{occupationDetailLabel}</Label>
										<Input
											id="occupation_detail"
											name="occupation_detail"
											value={c.occupation_detail ?? ''}
											placeholder={occupationDraft === 'Retired'
												? 'What they did before retiring'
												: 'Describe the occupation'}
										/>
									</div>
								{/if}
								<div class="space-y-1">
									<Label for="country_of_birth">Country of birth</Label>
									<Combobox
										id="country_of_birth"
										name="country_of_birth"
										bind:value={countryOfBirthDraft}
										options={countryOptions}
										placeholder="Select a country…"
									/>
								</div>
								<div class="space-y-1">
									<Label for="town_of_birth">Town of birth</Label>
									<Input
										id="town_of_birth"
										name="town_of_birth"
										value={c.town_of_birth ?? ''}
										placeholder="Town or city"
									/>
								</div>
								<!-- Nationality is MULTI (two passports); tax residency is ONE country,
								     the same combobox onboarding already asks. Both store ISO codes
								     from `ref.countries` — posting the code and showing the name is
								     what stops a firm typing "United Kingdom" into a column holding `GB`.
								     The tax-residency column is still `text[]`; the action wraps the
								     one code as `[code]`. -->
								<div class="space-y-1">
									<Label>Nationalities</Label>
									<input type="hidden" name="nationality" value="" />
									<!-- ADJECTIVES, not country names: a nationality is a property of the person
									     and reads as an adjective in every document a firm produces, where the
									     country NAME answers a different question (tax residency, birth). A shared
									     adjective is disambiguated with the country, since "Chinese" alone cannot
									     be posted as a code. -->
									<MultiSelect
										name="nationality"
										bind:value={nationalities}
										options={nationalityChoices}
										placeholder="Select nationalities"
										onOpenChange={(open) => {
											if (open) void ensureCountries();
										}}
									/>
								</div>
								<div class="space-y-1">
									<!-- THE TYPE IS DERIVED FROM NATIONALITY AND POSTED AS A HIDDEN FIELD. It is
									     the regulator's choice, not the adviser's, so offering a box invites a
									     record that reports one scheme's number under another's name. After
									     nationalities because the identifier is decided from them; one column,
									     not a band — it is one short code, not a paragraph. -->
									<input
										type="hidden"
										name="national_identifier_type"
										value={identifierSpec?.name ?? ''}
									/>
									<Label for="national_identifier">
										{identifierSpec?.name ?? 'National identifier'}
									</Label>
									{#if isConcat}
										<!-- COMPUTED, never typed: the format's whole purpose is that two firms
										     holding one person derive the same string. Read-only, and it follows
										     the name and date fields as they are corrected. -->
										<Input
											id="national_identifier"
											name="national_identifier"
											value={identifierValue}
											readonly
											class="bg-muted/40"
										/>
										<p class="text-muted-foreground text-xs">
											{#if concat}
												Derived from the date of birth and name — CONCAT is the identifier
												{identifierCountry ?? 'this nationality'} uses.
											{:else}
												A date of birth, a first name and a surname are needed to derive it.
											{/if}
										</p>
									{:else}
										<Input
											id="national_identifier"
											name="national_identifier"
											bind:value={nationalIdentifierDraft}
											placeholder={identifierSpec?.example ?? ''}
											aria-invalid={nationalIdentifierError ? true : undefined}
											pattern={isNinoType(identifierSpec?.name)
												? NATIONAL_INSURANCE_PATTERN
												: undefined}
											title={isNinoType(identifierSpec?.name)
												? NATIONAL_INSURANCE_MESSAGE
												: undefined}
										/>
										{#if nationalIdentifierError}
											<p class="text-destructive text-xs">{nationalIdentifierError}</p>
										{/if}
										{#if identifierSpec}
											<p class="text-muted-foreground text-xs">
												Required for a {byCountryCode.get(identifierCountry ?? '')?.adjective ??
													identifierCountry} national.
											</p>
										{:else}
											<p class="text-muted-foreground text-xs">
												Choose a nationality and the identifier this client must give is decided
												from it.
											</p>
										{/if}
									{/if}
								</div>
								<div class="space-y-1">
									<Label>Tax residencies</Label>
									<input type="hidden" name="tax_residency" value="" />
									<MultiSelect
										name="tax_residency"
										bind:value={taxResidencies}
										options={countryOptions}
										placeholder="Select tax residencies"
										onOpenChange={(open) => {
											if (open) void ensureCountries();
										}}
									/>
								</div>
								{@render saveBar()}
							</form>
						{:else}
							{@render register(
								rows(
									['Date of birth', c.dob ? formatDate(c.dob) : null],
									['Gender', closedLabel(GENDER_OPTIONS, c.gender)],
									['Occupation', c.occupation],
									...(c.occupation === 'Other' || c.occupation === 'Retired'
										? ([
												[
													c.occupation === 'Retired' ? 'Previous occupation' : 'Please specify',
													c.occupation_detail
												]
											] as [string, unknown][])
										: []),
									['Country of birth', countryName(c.country_of_birth)],
									['Town of birth', c.town_of_birth],
									['Nationalities', countryNames(c.nationality)],
									[
										c.national_identifier_type
											? label(c.national_identifier_type)
											: 'National identifier',
										c.national_identifier
									],
									['Tax residency', countryNames(c.tax_residency)]
								)
							)}
						{/if}
					{/snippet}
					{@render sectionCard(
						'personal',
						'Personal details',
						personalBody,
						null,
						section !== 'overview',
						'client',
						holdsFor('personal')
					)}
				{/if}

				{#if showDetailCards}
					<!-- PENSION, shown for every individual regardless of what they hold — the record
				     COMPLETES the picture rather than inferring what is worth asking, same as every
				     other section here (see docs/client-record.md §1). -->
					{#if !isOrg}
						{#snippet pensionBody(editing: boolean)}
							{#if editing}
								<form
									method="POST"
									action={actions?.updateClient}
									use:enhanceForm={withToast(savedOrHeld('Pension details updated'), {
										pending: (v) => (saving = v),
										reset: false,
										onSuccess: () => (editingSection = null)
									})}
									class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
								>
									<input type="hidden" name="type" value={c.type} />
									<div class="space-y-1">
										<Label for="marital_status">Marital status</Label>
										<SelectMenu
											id="marital_status"
											name="marital_status"
											items={[{ value: '', label: '—' }, ...maritalChoices]}
											bind:value={maritalDraft}
										/>
									</div>
									<div class="space-y-1">
										<Label for="employment_status">Employment status</Label>
										<SelectMenu
											id="employment_status"
											name="employment_status"
											items={[{ value: '', label: '—' }, ...employmentChoices]}
											bind:value={employmentDraft}
										/>
									</div>
									<div class="space-y-1">
										<Label for="retirement_age">Retirement age</Label>
										<Input
											id="retirement_age"
											name="retirement_age"
											inputmode="numeric"
											pattern="[0-9]*"
											autocomplete="off"
											bind:value={
												() => retirementAgeDraft, (v) => (retirementAgeDraft = v.replace(/\D/g, ''))
											}
										/>
									</div>
									<div class="space-y-1">
										<!-- "Annual earnings" is the label; `relevant_uk_earnings` is the column.
									     The statutory name is what the relief maths computes with — not
									     everything on a P60 is relievable — and matches Morgan Lloyd's
									     `cam_annualearnings`. -->
										<Label for="relevant_uk_earnings">Annual earnings</Label>
										<MoneyInput
											id="relevant_uk_earnings"
											name="relevant_uk_earnings"
											value={c.relevant_uk_earnings ?? undefined}
										/>
									</div>
									<div class="space-y-1">
										<Label for="tax_relief">Tax relief declared</Label>
										<SelectMenu
											id="tax_relief"
											name="tax_relief"
											items={[
												{ value: '', label: '—' },
												...Object.entries(TAX_RELIEF_LABELS).map(([value, text]) => ({
													value,
													label: text
												}))
											]}
											bind:value={taxReliefDraft}
										/>
									</div>
									<div class="space-y-1 sm:col-span-2 lg:col-span-3">
										<!-- Asked rather than derived from the ledger: a member whose employer is
									     about to start contributing has no rows yet and still needs the company
									     captured before the first payment. A Switch, not a checkbox: Switch
									     renders a button, so the posted value is a hidden field. Control on
									     the left, matching flexibly-accessed benefits beside it. -->
										<input type="hidden" name="employer_contributions_present" value="1" />
										{#if showEmployerSection}
											<input type="hidden" name="employer_contributions" value="on" />
										{/if}
										<div class="flex items-center gap-3">
											<Switch
												id="employer_contributions"
												checked={showEmployerSection}
												onCheckedChange={(on) => (employerContributions = on === true)}
											/>
											<Label for="employer_contributions" class="m-0 font-medium">
												Receives employer contributions
											</Label>
										</div>
										<p class="text-muted-foreground text-xs">
											Morgan Lloyd needs the company before an employer contribution can be
											recorded.
										</p>
									</div>
									<div class="space-y-1 sm:col-span-2 lg:col-span-3">
										<input type="hidden" name="flexi_access_taken_present" value="1" />
										{#if showFlexiDate}
											<input type="hidden" name="flexi_access_taken" value="on" />
										{/if}
										<div class="flex items-center gap-3">
											<Switch
												id="flexi_access_taken"
												checked={showFlexiDate}
												onCheckedChange={(v) => (flexiAccessTaken = v === true)}
											/>
											<Label for="flexi_access_taken" class="m-0 font-medium">
												Has flexibly accessed pension benefits
											</Label>
										</div>
										<p class="text-muted-foreground text-xs">
											Cuts the annual allowance to £10,000 (the MPAA).
										</p>
									</div>
									{#if showFlexiDate}
										<div class="space-y-1">
											<!-- WHEN is a different fact from WHETHER: the date decides which tax
										     year the reduced allowance first applies to, so a contribution
										     booked for the year before is unaffected. -->
											<Label for="flexi_access_date">Date benefits were accessed</Label>
											<Input
												id="flexi_access_date"
												name="flexi_access_date"
												type="date"
												autocomplete="off"
												class="[&::-webkit-calendar-picker-indicator]:cursor-pointer dark:[&::-webkit-calendar-picker-indicator]:invert"
												value={c.flexi_access_date ?? ''}
											/>
										</div>
									{/if}
									{@render saveBar()}
								</form>
							{:else}
								{@render register(
									withMoney(
										rows(
											['Marital status', closedLabel(MARITAL_OPTIONS, c.marital_status)],
											['Employment status', closedLabel(EMPLOYMENT_OPTIONS, c.employment_status)],
											['Retirement age', c.retirement_age],
											[
												'Annual earnings',
												c.relevant_uk_earnings != null ? formatGBP(c.relevant_uk_earnings) : null
											],
											['Employer contributions', yesNo(c.employer_contributions)],
											[
												'Tax relief declared',
												c.tax_relief
													? (TAX_RELIEF_LABELS[c.tax_relief as TaxRelief] ?? c.tax_relief)
													: null
											],
											['Flexibly accessed benefits', yesNo(c.flexi_access_taken)],
											[
												'Date accessed',
												c.flexi_access_date ? formatDate(c.flexi_access_date) : null
											]
										),
										{ 'Annual earnings': c.relevant_uk_earnings }
									)
								)}
							{/if}
						{/snippet}
						{@render sectionCard(
							'pension',
							'Pension details',
							pensionBody,
							null,
							true,
							'client',
							holdsFor('pension')
						)}
					{/if}

					<!-- EMPLOYER, shown for every individual — the company is the payer of an employer
				     contribution (`cam_employerothername`), which is not the member, so it is its own
				     section rather than three more fields on the person. No longer gated on the
				     "Receives employer contributions" switch: that answer still saves as a normal field,
				     but this record completes the picture rather than hiding a section pending an answer. -->
					{#if !isOrg}
						{#snippet employerBody(editing: boolean)}
							{#if editing}
								<form
									method="POST"
									action={actions?.updateClient}
									use:enhanceForm={onEmployerSubmit}
									autocomplete="off"
									class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
								>
									<input type="hidden" name="type" value={c.type} />
									<div class="space-y-1 sm:col-span-2">
										<Label for="employer_name">Company name</Label>
										<Input id="employer_name" name="employer_name" value={c.employer_name ?? ''} />
									</div>
									<div class="space-y-1">
										<!-- TEXT, not a number: a company number is SC/NI-prefixed outside England
									     and Wales and its leading zeros are significant (00445790). -->
										<Label for="employer_registration_number">Company registration number</Label>
										<Input
											id="employer_registration_number"
											name="employer_registration_number"
											value={c.employer_registration_number ?? ''}
											placeholder="e.g. 00445790"
										/>
									</div>
									<!-- The employer's bank account: the same inputs and the same live sort-code
									     check as the client's own bank card. UK only — it is where employer
									     contributions come FROM, so there is no rail or payment method to choose. -->
									<div class="min-w-0 space-y-1">
										<Label for="employer-bank-name">Bank account name</Label>
										<Input
											id="employer-bank-name"
											name="employer_bank_name"
											bind:value={employerBankNameDraft}
											autocomplete="off"
											aria-invalid={employerBankErrors.name ? 'true' : undefined}
										/>
										{#if employerBankErrors.name}
											<p class="text-destructive text-xs">{employerBankErrors.name}</p>
										{/if}
									</div>
									<div class="min-w-0 space-y-1">
										<Label for="employer-bank-sort">Sort code</Label>
										<SortCodeInput
											id="employer-bank-sort"
											name="employer_bank_sort_code"
											value={employerBankSortDraft}
											invalid={Boolean(employerBankErrors.sort_code)}
											verified={employerBankStatus === 'confirmed'}
											onchange={(v) => (employerBankSortDraft = v)}
										/>
										{#if employerBankErrors.sort_code}
											<p class="text-destructive text-xs">{employerBankErrors.sort_code}</p>
										{/if}
									</div>
									<div class="min-w-0 space-y-1">
										<Label for="employer-bank-number">Account number</Label>
										<BankAccountNumberInput
											id="employer-bank-number"
											name="employer_bank_account_number"
											value={employerBankNumberDraft}
											invalid={Boolean(employerBankErrors.account_number)}
											verified={employerBankStatus === 'confirmed'}
											mismatch={employerBankStatus === 'rejected' &&
												employerBankResult?.state !== 'sort_code_unknown'}
											onchange={(v) => (employerBankNumberDraft = v)}
										/>
										{#if employerBankErrors.account_number}
											<p class="text-destructive text-xs">{employerBankErrors.account_number}</p>
										{/if}
									</div>
									<div class="col-span-full">
										<BankAccountCheck
											sortCode={employerBankSortDraft}
											accountNumber={employerBankDigits}
											bind:status={employerBankStatus}
											bind:result={employerBankResult}
										/>
									</div>
									{@render addressEditor({
										lookupId: 'employer_addr_lookup',
										prefix: 'employer_addr',
										pick: employerAddressPick,
										setPick: (v) => (employerAddressPick = v),
										stored: c.employer_address,
										country: employerAddressCountry,
										setCountry: (v) => (employerAddressCountry = v),
										editing: editingEmployerAddress,
										setEditing: (v) => (editingEmployerAddress = v)
									})}
									{@render saveBar()}
								</form>
							{:else}
								{@render register(
									rows(
										['Company name', c.employer_name],
										['Registration number', c.employer_registration_number],
										['Address', employerAddressLine || null],
										// The scheme's account, with the bank the directory named. No payment
										// method: that row is about where a withdrawal goes.
										...(employerBank
											? bankDisplayRows(employerBank).filter(
													([label]) => label !== 'Payment method'
												)
											: ([['Bank account', null]] as [string, unknown][]))
									)
								)}
							{/if}
						{/snippet}
						{@render sectionCard(
							'employer',
							'Employer details',
							employerBody,
							'The payer of an employer contribution — Morgan Lloyd needs the company, not the member.',
							true,
							'client',
							holdsFor('employer')
						)}
					{/if}

					<!-- Its own card, and its own save (`setVulnerability`), for the same reason the
					     drawer is not a field on `updateClient`: this is special-category-adjacent
					     data with its own endpoint. The pencil opens that drawer. The header banner
					     stays, so the flag is still the first thing read on every page. -->
					<Card glass size="sm" class="gap-2 py-3" id="vulnerability">
						<CardHeader class="flex flex-row items-start justify-between gap-3 pb-0">
							<div class="min-w-0">
								<CardTitle class="text-base">Vulnerability</CardTitle>
								<p class="text-muted-foreground mt-0.5 text-xs">
									The FCA drivers that apply, and whether they are temporary.
								</p>
							</div>
							{#if actions?.setVulnerability}
								{@render editPencil(
									openVulnerability,
									() => (openVulnerability ? (openVulnerability = false) : editVulnerability()),
									'vulnerability'
								)}
							{/if}
						</CardHeader>
						<CardContent>
							{#if pendingVulnerability}
								<!-- One hold per record for this card: the card says it is modified and
								     waiting, the register still says what the record holds NOW, the proposal
								     follows it, and another edit UPDATES the case. -->
								<ReviewHoldBanner
									review={pendingVulnerability}
									what="Vulnerability"
									href={links.review?.(pendingVulnerability.uid) ?? null}
									cancelAction={pendingVulnerability.is_mine
										? (actions?.cancelReview ?? null)
										: null}
									enhance={pendingVulnerability.is_mine && actions?.cancelReview
										? enhanceForm
										: null}
									class="mb-3"
								/>
							{/if}
							{@render register(
								rows(
									['Vulnerable', yesNo(c.is_vulnerable)],
									['Drivers', vulnerabilityDrivers],
									['Characteristics', vulnerabilityCharacteristics],
									['Duration', vulnerabilityDuration],
									['Notes', c.vulnerability_notes],
									['Recorded', vulnRecorded]
								)
							)}
							{#if pendingVulnerability}
								<div class="border-border/60 mt-3 border-t pt-3">
									<p class="text-muted-foreground mb-1.5 text-xs">
										Proposed in {reviewCaseRef(pendingVulnerability.case_no)} — now and proposed
									</p>
									<ReviewChange
										review={pendingVulnerability}
										showClient={false}
										showAccount={false}
										showTitle={false}
									/>
								</div>
							{/if}
						</CardContent>
					</Card>
					{#if gathered.length}
						<!-- THE FACT-FIND'S OVERFLOW — answers with no column of their own, nested under
					     `crm.contacts.data` by the onboarding mapping. Outside the five sections on
					     purpose: these are a FIRM'S own added questions, so the record cannot know what
					     they mean or which section they belong to, and inventing a home for them would
					     be a worse claim than showing them as gathered. Kept behind a disclosure and
					     kept at all — write-only was the wrong answer (0116). -->
						<details class="px-1">
							<summary class="text-muted-foreground hover:text-foreground cursor-pointer text-xs">
								{gathered.length} more from the fact-find
							</summary>
							<dl class="mt-2 grid gap-x-8 text-xs sm:grid-cols-2 lg:grid-cols-3">
								{#each gathered as g (g.label)}
									<div
										class="border-border/50 flex items-baseline justify-between gap-3 border-b py-1.5"
									>
										<dt class="text-muted-foreground shrink-0 text-xs">{g.label}</dt>
										<dd class="min-w-0 truncate text-right" title={g.value}>{g.value}</dd>
									</div>
								{/each}
							</dl>
						</details>
					{/if}
				{/if}
			</section>

			<!-- Bank details: one set per person, used by every account they hold (the account detail
			     page renders them). -->
			{#if showOverviewMoney}
				<section id="accounts">
					<!-- No heading. It said "Accounts 8" above a table whose every row is an account, and the
				     count is now on the toolbar where the filtered total can be honest about what it is
				     counting. The toolbar itself is the same `ListingToolbar` the /accounts book uses, so a
				     client's eight accounts are searched and narrowed exactly the way the firm's four hundred
				     are — the reason it was missing is that eight rows never needed it, which stops being
				     true the moment a client holds thirty. -->
					<Card glass>
						<CardContent class="space-y-4">
							<ListingToolbar
								bind:query={accountQuery}
								placeholder="Search accounts, reference or model"
								dirty={accountsDirty}
								onclear={clearAccountFilters}
								{filterDefs}
								filterValue={accountFilterValue}
								onfilters={onAccountFilters}
							>
								{#snippet filters()}
									<span class="text-muted-foreground ms-auto shrink-0 text-xs tabular-nums">
										{visibleAccounts.length === detail.accounts.length
											? `${detail.accounts.length} ${detail.accounts.length === 1 ? 'account' : 'accounts'}`
											: `${visibleAccounts.length} of ${detail.accounts.length}`}
									</span>
								{/snippet}
							</ListingToolbar>
							<DataTable
								columns={accountColumns}
								rows={visibleAccounts}
								bind:sort={accountSort}
								getKey={(a) => a.id}
								empty={detail.accounts.length === 0
									? 'This client’s accounts appear here once they’re opened.'
									: noAccountMatches}
							>
								{#snippet cell(a, column)}
									{#if column.key === 'name'}
										<!-- `TruncatedText` rather than a bare `truncate`: clipping a name without a way to
									     read the rest does not shorten it, it HIDES it, and a native `title` is
									     unreachable by keyboard. It renders the anchor itself when given an href, so the
									     row keeps its middle-click. -->
										<span class="block min-w-0">
											<TruncatedText
												text={a.name ?? providerReference(a.uid) ?? `#${a.id}`}
												href={links.account(a.uid)}
												class="font-medium"
											/>
											<RecordId value={providerReference(a.uid)} />
										</span>
									{:else if column.key === 'service'}
										{@const service = serviceTypeLabel(a.service_type)}
										<!-- An em-dash, never a guess: an account with neither a captured capacity nor
									     a model cannot be told apart from an execution-only one. -->
										{#if service}
											<Badge variant="outline" class="max-w-full min-w-0 font-normal"
												><TruncatedText text={service} /></Badge
											>
										{:else}
											<span class="text-muted-foreground text-xs">—</span>
										{/if}
									{:else if column.key === 'type'}
										{#if a.product_type}
											<!-- Capped the same way as the Accounts listing's Type (FB-20260921-09). -->
											<Badge variant="outline" class="max-w-full min-w-0 font-normal"
												><TruncatedText text={humanise(a.product_type)} /></Badge
											>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/if}
									{:else if column.key === 'model'}
										{#if a.model_name}
											<TruncatedText text={a.model_name} />
										{:else}
											<!-- An em-dash, not "Advisory". A missing model_id is not a service
										     agreement, and printing one here called every execution-only account
										     advised — the same claim the account record used to make. The Service
										     type column beside this is where that question is answered. -->
											<span class="text-muted-foreground">—</span>
										{/if}
									{:else if column.key === 'status'}
										<!-- The shared status map, not a bare outline badge: an account's status has to
									     look the same here as it does on the accounts listing and the transactions
									     ledger, or the colour means nothing. A toneless badge is the mistake
									     `status-tone.ts` was written to end — a healthy account and a draft one read
									     identically, so the column carried nothing at a glance. -->
										<StatusBadge tone={statusTone(a.status)}>{humanise(a.status)}</StatusBadge>
									{:else if column.key === 'value'}
										<Money
											amount={a.value}
											currency={a.currency || baseCurrency}
											class="font-medium"
										/>
									{:else if column.key === 'cash'}
										<Money amount={a.cash} currency={a.currency || baseCurrency} />
									{:else if column.key === 'growth'}
										<Money
											amount={a.growth}
											currency={a.currency || baseCurrency}
											signed
											tone="directional"
										/>
									{/if}
								{/snippet}
							</DataTable>
						</CardContent>
					</Card>
				</section>
			{/if}

			<!-- Bank details live on the person (the Details band), not here: one set per client,
			     used by every account they hold when the account has none of its own. The account
			     record still shows them, and says so when it is inheriting. -->

			<!-- Standing arrangements across every account: a regular payment is something the
			     client set up. Rendered only when there is something to say. -->
			{#if standing.length > 0}
				<section id="standing">
					<!-- The same card title as Contact details and the rest of this page. A section
					     heading sat outside the card at a smaller size, so this band did not belong
					     to the page it was on. -->
					<Card glass size="sm" class="gap-2 py-3">
						<CardHeader class="pb-0">
							<CardTitle class="text-base">Regular payments &amp; withdrawals</CardTitle>
						</CardHeader>
						<CardContent>
							<DataTable
								columns={standingColumns}
								rows={standing}
								getKey={(r) => r.key}
								dense
								empty="No regular payments or withdrawals set up."
							>
								{#snippet cell(r, column)}
									{#if column.key === 'direction'}
										<!-- Which WAY the money goes is the first thing read, so it is the first
										     column and it is a word rather than a sign. -->
										<Badge variant={r.direction === 'out' ? 'outline' : 'secondary'}>
											{r.direction === 'out' ? 'Withdrawal' : 'Payment'}
										</Badge>
									{:else if column.key === 'account'}
										<a href={links.account(r.accountUid)} class="block min-w-0 hover:underline">
											<span class="block truncate">{r.accountName}</span>
										</a>
									{:else if column.key === 'account_id'}
										<span class="text-muted-foreground font-mono text-xs">{r.accountUid}</span>
									{:else if column.key === 'schedule'}
										<span class="whitespace-nowrap">{scheduleLabel(r)}</span>
									{:else if column.key === 'amount'}
										<Money amount={r.amount ?? 0} class="font-medium" />
									{:else if column.key === 'auto_invest'}
										<span class="text-muted-foreground">{autoInvestLabel(r)}</span>
									{/if}
								{/snippet}
							</DataTable>
						</CardContent>
					</Card>
				</section>
			{/if}
		</div>
	</div>

	<div class="contents lg:flex lg:w-[22rem] lg:shrink-0 lg:flex-col lg:gap-6">
		<!-- TEAM — who services this client, with the faces that identify them elsewhere in the
		     product. Collapsed is the stack this card has always shown; expanded is one row per
		     person. The overflow menu opens the assignments (the cog used to); it sits OUTSIDE the
		     disclosure trigger so expanding the list cannot steal the way to change who is on the
		     book. With no assign/unassign action the dialog still opens, read-only, because who
		     looks after a client is worth reading on a mount that cannot change it. -->
		<Card glass class="order-2 min-w-0">
			<CardContent class="min-w-0">
				<Collapsible.Root bind:open={teamOpen} class="group/team min-w-0">
					<div class="flex items-center justify-between gap-3">
						<Collapsible.Trigger
							class="focus-visible:ring-ring/50 text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-1 rounded-md text-xs font-medium outline-none focus-visible:ring-2"
						>
							Team
							<ChevronDownIcon
								class="size-3.5 transition-transform duration-200 group-data-[state=closed]/team:-rotate-90"
							/>
						</Collapsible.Trigger>
						<DropdownMenu.Root>
							<DropdownMenu.Trigger
								class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -my-1 -mr-1 inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-md outline-none focus-visible:ring-2"
								title="Team actions"
								aria-label="Team actions"
							>
								<EllipsisIcon class="size-4" />
							</DropdownMenu.Trigger>
							<DropdownMenu.Content align="end" class="w-44">
								<DropdownMenu.Item
									onclick={() => {
										openAdvisers = true;
										void ensureMembers();
									}}>Manage team</DropdownMenu.Item
								>
							</DropdownMenu.Content>
						</DropdownMenu.Root>
					</div>
					<div class="mt-1.5 group-data-[state=open]/team:hidden">
						{#if team.length === 0}
							<p class="text-muted-foreground text-xs">Nobody assigned</p>
						{:else}
							<AdviserStack names={team.map((a) => a.name ?? a.email ?? '')} size="sm" />
						{/if}
					</div>
					<Collapsible.Content class="mt-1.5">
						{#if team.length === 0}
							<p class="text-muted-foreground text-xs">Nobody assigned</p>
						{:else}
							<ul class="divide-border divide-y text-xs">
								{#each team as adv, i (adv.adviser_id ?? adv.email ?? i)}
									{@const roleWord = clientTeamRoleLabel(adv.role)}
									<li class="flex items-center gap-2 py-2 first:pt-0 last:pb-0">
										<PersonAvatar name={adv.name ?? adv.email ?? '?'} size="sm" />
										<span class="min-w-0 flex-1">
											<span class="flex min-w-0 items-center gap-2">
												<span class="min-w-0 truncate font-medium"
													>{adv.name ?? adv.email ?? '—'}</span
												>
												<!-- The tag is the ROLE on this client, not the staff flag: someone who may
												     advise can sit on this team as Support, and only one person is the
												     client's adviser. -->
												{#if adv.role === 'adviser'}
													<Badge variant="secondary" class="shrink-0 font-normal">Adviser</Badge>
												{:else if roleWord}
													<Badge variant="outline" class="shrink-0 font-normal">{roleWord}</Badge>
												{/if}
											</span>
											{#if adv.job_title}
												<span class="text-muted-foreground block truncate text-xs"
													>{adv.job_title}</span
												>
											{/if}
										</span>
									</li>
								{/each}
							</ul>
						{/if}
					</Collapsible.Content>
				</Collapsible.Root>
			</CardContent>
		</Card>
		<div class="order-3 min-w-0">
			{@render sectionCard(
				'compliance',
				'Compliance',
				complianceBody,
				null,
				!isOrg,
				'client',
				holdsFor('compliance')
			)}
		</div>

		<!-- Rail: allocation, action items, notes. A flex column as tall as what is in it —
		     stretching it to the main column's height is what made a Notes card holding one
		     note a 700px empty box. -->
		<div class="order-5 flex min-w-0 flex-col gap-4">
			{#if showOverviewMoney}
				{#if holdingsError}
					<!-- The ring and the Cash figure both come from the lake; an outage must not read as
				     "this client holds nothing". -->
					<LoadErrorBanner
						message="The provider position couldn’t be loaded, so cash and allocation may be incomplete."
						onRetry={onretry}
					/>
				{/if}
				<!-- NO SCREENING CARD. Identity and AML screening is GRAPHENE's function as platform provider,
			     not the firm's — the firm sees the outcome of a review, never the review (docs/aml.md), and
			     `screening:read` already gives them the dedicated /compliance page for that. On the record
			     it was the first thing in the rail, which put our compliance process above the client's own
			     money in a firm-facing view. `screeningChecks` stays in the contract; nothing is unwired.
			-->
				<!-- ALLOCATION FIRST. How the money is arranged is the thing this rail is read for, and it is
			     the only card here that is about the client rather than about work outstanding — so it goes
			     above the action items, which are a queue and belong nearer the notes you write about them.
			     Screening used to hold this slot. -->
				{#if allocSegments.length}
					<Card glass class="gap-2">
						<CardHeader><CardTitle class="text-base">Allocation</CardTitle></CardHeader>
						<CardContent>
							<!-- The SAME ring the account record draws, component and all: it was thirty-five
							     lines of SVG copied between the two records, and the copies had already drifted
							     on how precisely they state a share. No money column — this rail has not the
							     width, and the figure would repeat the portfolio value two cards above. -->
							<AllocationDonut segments={allocSegments} />
						</CardContent>
					</Card>
				{/if}

				{#if expectedIn.length}
					<Card glass class="gap-2">
						<CardHeader>
							<CardTitle class="text-base">Expected in</CardTitle>
							<CardAction>
								<Badge variant="outline" class="text-warning">{expectedIn.length} pending</Badge>
							</CardAction>
						</CardHeader>
						<CardContent>
							{#if expectedCurrency}
								<p class="text-xl font-semibold tabular-nums">
									<Money amount={expectedTotal} currency={expectedCurrency} tone="money" />
								</p>
							{/if}
							<ul class="text-muted-foreground mt-2 space-y-1 text-xs">
								<!-- The account is spelt out rather than joined into the string, so it can be the
								     link: this card says money is coming INTO an account, and the account is the
								     thing a reader goes to next. The `·` separators and the drop-what-is-missing
								     behaviour are unchanged — each part still only renders when it has a value. -->
								{#each expectedIn as e (e.uid)}
									<li>
										<Money amount={e.amount} currency={accountCurrency(e.account_id)} />
										{#if e.account_name}
											·
											{#if e.account_uid && links.account}
												<a href={links.account(e.account_uid)} class={LINK_RECORD}
													>{e.account_name}</a
												>
											{:else}
												{e.account_name}
											{/if}
										{/if}
										{#if e.due_date}· due {e.due_date}{/if}
									</li>
								{/each}
							</ul>
						</CardContent>
					</Card>
				{/if}

				<!-- NO MODELS CARD. It listed the distinct model names across the client's accounts, which is
			     a fact about each ACCOUNT and is already the Model column of the accounts table below —
			     the same words, twice, and the table's version says which account holds which. -->

				{#if tasks.length}
					<Card glass class="gap-2">
						<CardHeader>
							<CardTitle class="flex items-center justify-between text-base">
								Action items
								{#if links.tasks}
									<a href={links.tasks} class="text-brand text-xs font-medium hover:underline">
										All tasks
									</a>
								{/if}
							</CardTitle>
						</CardHeader>
						<CardContent class="space-y-2">
							{#each tasks as t (t.uid)}
								<svelte:element
									this={links.task ? 'a' : 'div'}
									href={links.task?.(t.uid)}
									class="hover:bg-muted/40 focus-visible:ring-ring -mx-1 flex items-center gap-2 rounded px-1 py-1 text-xs focus-visible:ring-2 focus-visible:outline-none"
								>
									<span
										class="size-2 shrink-0 rounded-full {t.overdue
											? 'bg-destructive'
											: 'bg-warning'}"
									></span>
									<span class="min-w-0 flex-1 truncate">{t.title}</span>
									{#if t.dueLabel}
										<span
											class="shrink-0 text-xs {t.overdue
												? 'text-destructive font-medium'
												: 'text-muted-foreground'}"
										>
											{t.dueLabel}
										</span>
									{/if}
								</svelte:element>
							{/each}
						</CardContent>
					</Card>
				{/if}
			{/if}
			<!-- Notes, last in the rail — at the rail's
			     own width, styled like its new neighbours (title inside a CardHeader, not an
			     external SectionHeading — a plain widget card, same as Allocation above,
			     rather than the "band" look Accounts/Details/Bank details use). `id="notes"`
			     stays on the card itself: the legacy `?tab=notes` deep link still needs
			     something to scroll to, and an id doesn't care what element carries it.
			     NO `flex-1` ANY MORE. It used to be here so the last card in the rail absorbed
			     whatever height the grid's stretch gave the column — which reads fine on a
			     client with a dozen notes and, on one with a single note, drew a bordered box
			     down the rest of the screen with nothing in it. The rail is `self-start` now,
			     so there is no leftover height to absorb and this card is as tall as its own
			     content. The list itself was never the problem: it pages at five.

			     `min-w-0` on both the card and its content: a flex item's default automatic
			     minimum size is its content's min-content width, which for an unbroken run of
			     characters (no spaces to wrap on) is the whole run — wide enough to push this
			     card past the rail's fixed 22rem track and get visibly clipped at its edge,
			     regardless of `whitespace-pre-wrap` below (that only controls WHERE a line
			     breaks, not whether the box is allowed to shrink narrower than its content in
			     the first place). `break-words` on the note body is the other half: it lets an
			     unbroken run itself wrap mid-word once the box can no longer grow to fit it,
			     rather than overflowing sideways. Together: notes still wrap fully, multi-line,
			     nothing hidden — this fixes the overflow, not the readability. -->
			<Card glass id="notes" class="flex min-w-0 flex-col gap-2">
				<CardHeader>
					<CardTitle class="text-base">Notes</CardTitle>
				</CardHeader>
				<CardContent class="min-w-0 space-y-4">
					{#if actions?.addNote}
						<form
							method="POST"
							action={actions.addNote}
							use:enhanceForm={withToast('Note added', {
								pending: (v) => (noting = v),
								// New notes sort newest-first (`crm.contact_notes`'s own ordering) — land
								// back on page 1 so the one just added is actually visible, rather than
								// leaving the reader on whatever page they were paginated to.
								onSuccess: (data) => {
									const note = asNote(data?.note);
									if (note) notes = [note, ...notes.filter((n) => n.uid !== note.uid)];
									notesPage = 1;
								}
							})}
						>
							<!-- Shared with the transfer detail: the darker field, and a ghost note icon. -->
							<NoteComposer
								name="body"
								placeholder="Add a note about this client…"
								required
								pending={noting}
								onkeydown={(e) => {
									if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
										e.preventDefault();
										if (e.currentTarget instanceof HTMLTextAreaElement) {
											e.currentTarget.form?.requestSubmit();
										}
									}
								}}
							/>
						</form>
					{/if}
					{#if notes.length > 0}
						<ul class="space-y-4">
							{#each notesPageItems as n (n.id)}
								{@const expanded = expandedNoteIds.includes(n.id)}
								{@const overflowing = overflowingNoteIds.includes(n.id)}
								<li class="group/note relative border-b pb-4 last:border-0 last:pb-0">
									{#if actions?.deleteNote && n.uid}
										<!-- TOP RIGHT, and only on hover/focus: a delete on every row of a
										     scrolling list is a row of crosses inviting a mis-click, where an
										     adviser removing their own note knows which one they mean.
										     `focus-within` as well as hover, so it is reachable by keyboard. -->
										<form
											method="POST"
											action={actions.deleteNote}
											use:enhanceForm={withToast('Note deleted', {
												onSuccess: (data) => {
													const uid = String(data?.note_uid ?? '');
													if (uid) notes = notes.filter((row) => row.uid !== uid);
												}
											})}
											class="absolute top-0 right-0 opacity-0 transition-opacity group-focus-within/note:opacity-100 group-hover/note:opacity-100"
										>
											<input type="hidden" name="note_uid" value={n.uid} />
											<Button
												type="submit"
												variant="ghost"
												size="icon-sm"
												class="text-muted-foreground hover:text-destructive"
												title="Delete this note"
												aria-label="Delete this note"
											>
												<XIcon class="size-3.5" />
											</Button>
										</form>
									{/if}
									<!-- `whitespace-pre-wrap` applies whether collapsed or expanded — a
									     note authored with line breaks (the Textarea is multi-line) must
									     keep them while clamped too, both so the collapsed preview reads
									     the way it was written rather than as flattened run-on text, and
									     so the overflow measurement below sees the same line-broken
									     layout the reader does: measuring against newline-flattened text
									     can make a short multi-line note fit `clientHeight` and register
									     as non-overflowing when its real, line-broken rendering would not.
									     Clamped to 2 lines collapsed (native line-clamp ellipsis, no JS
									     truncation to keep in sync), full height expanded — for EVERY
									     note, since a short one is untouched by a 2-line clamp either
									     way. Only a note measured (see `overflowingNoteIds` above) to
									     actually be cut off by that clamp renders as a `<button>` with the
									     chevron; a short note that wouldn't change on click renders as a
									     plain, non-interactive row instead of a button that does
									     nothing. -->
									<svelte:element
										this={overflowing ? 'button' : 'div'}
										type={overflowing ? 'button' : undefined}
										role={overflowing ? 'button' : undefined}
										class={cn(
											'flex w-full items-start gap-2 text-left',
											// Room for the delete button's own top-right corner (`icon-sm`, size-7):
											// without it, an overflowing note's expand chevron sits in that same
											// corner and the two controls land on top of each other on hover.
											actions?.deleteNote && n.uid && 'pr-7'
										)}
										onclick={overflowing ? () => toggleNote(n.id) : undefined}
										aria-expanded={overflowing ? expanded : undefined}
									>
										<span class="min-w-0 flex-1">
											<p
												bind:this={noteBodyEls[n.id]}
												class={cn(
													'text-xs whitespace-pre-wrap break-words',
													!expanded && 'line-clamp-2'
												)}
											>
												{n.body}
											</p>
											<p class="text-muted-foreground mt-1 text-xs">
												{n.author ?? 'Someone'} · {formatDate(n.created_at)}
											</p>
										</span>
										{#if overflowing}
											<ChevronDownIcon
												class={cn(
													'text-muted-foreground mt-0.5 size-4 shrink-0 transition-transform',
													expanded && 'rotate-180'
												)}
											/>
										{/if}
									</svelte:element>
								</li>
							{/each}
						</ul>
						<!-- Same layout as the book listings' pager (label left, numbered pages
						     right) — hidden on one page, same as there. -->
						{#if notesPageCount > 1}
							<div class="flex flex-wrap items-center justify-between gap-2 pt-1">
								<p class="text-muted-foreground text-xs tabular-nums" aria-live="polite">
									{notesRangeLabel}
								</p>
								<Pagination bind:page={notesPage} pageCount={notesPageCount} />
							</div>
						{/if}
					{/if}
				</CardContent>
			</Card>
			{#if showOverviewMoney}
				<RecentInstructions
					{instructions}
					accountHref={links.account}
					currencyOf={(instruction) => accountCurrency(instruction.account_id)}
					oncancel={actions?.cancelInstruction
						? (uid) => {
								cancelUid = uid;
								cancelOpen = true;
							}
						: undefined}
				/>
			{/if}
		</div>
	</div>
</div>

{#if actions?.cancelInstruction}
	<!-- Cancel-instruction confirmation: one hidden form serves every row; the dialog submits it. -->
	<form
		method="POST"
		action={actions.cancelInstruction}
		bind:this={cancelForm}
		use:enhanceForm={withToast('Instruction cancelled')}
		class="hidden"
	>
		<input type="hidden" name="uid" value={cancelUid ?? ''} />
	</form>
	<ConfirmDialog
		bind:open={cancelOpen}
		title="Cancel this instruction?"
		description="The pending instruction will be cancelled. This can’t be undone."
		confirmLabel="Cancel instruction"
		cancelLabel="Keep instruction"
		destructive
		onconfirm={() => cancelForm?.requestSubmit()}
	/>
{/if}

{#if actions?.pinClassification}
	<form
		method="POST"
		action={actions.pinClassification}
		bind:this={pinForm}
		use:enhanceForm={withToast('Client book updated')}
		class="hidden"
	>
		<input type="hidden" name="classification_uid" value={pinUid} />
	</form>
{/if}

{#if actions?.setGroups}
	<form
		method="POST"
		action={actions.setGroups}
		bind:this={groupsForm}
		use:enhanceForm={withToast('Client groups updated')}
		class="hidden"
	>
		{#each groupUids as uid (uid)}
			<input type="hidden" name="group_uid" value={uid} />
		{/each}
	</form>
{/if}

{#if actions?.invitePortal}
	<form
		method="POST"
		action={actions.invitePortal}
		bind:this={inviteForm}
		use:enhanceForm={withToast('Invitation sent', { pending: (v) => (inviting = v) })}
		class="hidden"
	></form>
{/if}

{#if actions?.restoreClient}
	<form
		method="POST"
		action={actions.restoreClient}
		bind:this={restoreForm}
		use:enhanceForm={withToast('Client reopened')}
		class="hidden"
	></form>
{/if}

{#if actions?.closeClient}
	<!-- Close. A soft delete: the row keeps its history, the record stays loadable, and Reopen
	     brings them back. Hidden unless every account is worth zero — the API refuses a valued
	     book for the same rule. -->
	<form
		method="POST"
		action={actions.closeClient}
		bind:this={closeForm}
		use:enhanceForm={withToast('Client closed', { pending: (v) => (closing = v) })}
		class="hidden"
	></form>
	<ConfirmDialog
		bind:open={closeOpen}
		title="Close this client?"
		description="They come off the live book and AUM. Reopen them from this record or the Closed filter; pending instructions must be cancelled first, and every account must be worth zero."
		confirmLabel="Close client"
		destructive
		onconfirm={() => closeForm?.requestSubmit()}
	/>
{/if}

{#if actions?.createInstruction && openAction}
	{@const meta = ACTIONS[openAction]}
	<Dialog.Root open onOpenChange={(o) => !o && (openAction = null)}>
		<Dialog.Content class="w-[92%] sm:max-w-md">
			<Dialog.Title>{meta.title}</Dialog.Title>
			<p class="text-muted-foreground text-sm">{meta.blurb}</p>
			{#if actionAccounts.length === 0}
				<p class="text-muted-foreground py-4 text-sm">
					{meta.needsModel
						? 'None of this client’s accounts carries a model. Set one from the accounts table.'
						: 'This client has no accounts to act on.'}
				</p>
				<div class="flex justify-end">
					<Button type="button" variant="ghost" onclick={() => (openAction = null)}>Close</Button>
				</div>
			{:else}
				<form
					method="POST"
					action={actions.createInstruction}
					use:enhanceForm={onInstructionSubmit}
					class="mt-2 min-w-0 space-y-3"
				>
					<input type="hidden" name="kind" value={openAction} />
					{#if openAction === 'withdrawal'}
						<input type="hidden" name="payment_method" value={withdrawalMethod} />
						<input type="hidden" name="pass_rail_fee" value={passRailFee ? 'true' : 'false'} />
					{/if}
					<div class="space-y-1">
						<Label for="ins_account">Account</Label>
						<SelectMenu
							id="ins_account"
							name="account_id"
							items={accountItems}
							bind:value={insAccount}
						/>
					</div>
					{#if openAction === 'withdrawal' && !withdrawalInternational}
						<div class="space-y-1.5">
							<Label>Payment method</Label>
							<Segmented
								bind:value={withdrawalUkMethod}
								options={BANK_METHOD_OPTIONS}
								label="Payment method"
								shape="square"
							/>
							<p class="text-muted-foreground text-xs">
								Defaults to the method saved on this bank account. This withdrawal can use the
								other.
							</p>
						</div>
					{/if}
					<div class="space-y-1">
						<Label for="ins_amount">Amount</Label>
						<MoneyInput id="ins_amount" name="amount" placeholder="0.00" bind:value={insAmount} />
						{#if headroom}
							<!-- The figure the request is actually checked against — provider leg, net of this
							     account's pending claims. Stated here so a refusal is never a surprise. -->
							<p class="text-muted-foreground text-xs">{headroom}</p>
						{/if}
					</div>
					{#if openAction === 'withdrawal' && withdrawalTariff > 0}
						<dl class="space-y-1.5 text-xs">
							<div class="flex items-baseline justify-between gap-3">
								<dt class="text-muted-foreground">
									{WITHDRAWAL_METHOD_LABEL[withdrawalMethod]} fee
								</dt>
								<dd><Money amount={withdrawalTariff} /></dd>
							</div>
						</dl>
						<div class="flex items-start gap-3">
							<Switch id="ins_pass_fee" class="mt-0.5" bind:checked={passRailFee} />
							<Label for="ins_pass_fee" class="m-0 font-medium"
								>Pass this fee on to the client</Label
							>
						</div>
						{#if !passRailFee}
							<p class="text-muted-foreground text-xs">
								The firm pays this fee. It is not taken from the client’s cash.
							</p>
						{/if}
						<div class="flex items-baseline justify-between gap-3 text-xs font-medium">
							<span>Total to be debited</span>
							<Money amount={withdrawalTotal} />
						</div>
					{/if}
					{#if meta.needsDue}
						<div class="space-y-1">
							<Label for="ins_due">Expected date</Label>
							<Input
								id="ins_due"
								name="due_date"
								type="date"
								autocomplete="off"
								class="[&::-webkit-calendar-picker-indicator]:cursor-pointer dark:[&::-webkit-calendar-picker-indicator]:invert"
							/>
						</div>
					{/if}
					<div class="space-y-1">
						<Label for="ins_note">Note (optional)</Label>
						<Input id="ins_note" name="note" placeholder="e.g. annual ISA top-up" />
					</div>
					<div class="flex justify-end gap-2 pt-1">
						<Button type="button" variant="ghost" onclick={() => (openAction = null)}>Cancel</Button
						>
						<Button type="submit" disabled={instructionSubmitting}>
							{instructionSubmitting ? 'Working…' : meta.cta}
						</Button>
					</div>
				</form>
			{/if}
		</Dialog.Content>
	</Dialog.Root>
{/if}

<!-- TEAM, behind the overflow menu on the header's second card. What used to be the People
     page's first section: who looks after this client, and one row to assign another.
     It opens whatever the mount can do — the list always, the forms only with the actions — so a
     read-only mount answers "who advises them" without offering to change it. -->
{#if openAdvisers}
	<Dialog.Root open onOpenChange={(o) => !o && (openAdvisers = false)}>
		<Dialog.Content class="w-[92%] sm:max-w-lg">
			<Dialog.Title>Team</Dialog.Title>
			<p class="text-muted-foreground text-sm">Who looks after {name}.</p>
			{#if membersError}
				<LoadErrorBanner
					message="Your firm’s people couldn’t be loaded, so the picker may be incomplete."
					onRetry={onretry}
				/>
			{/if}
			{#if team.length === 0}
				<p class="text-muted-foreground text-xs">Nobody assigned.</p>
			{:else}
				<ul class="divide-border divide-y text-xs">
					{#each team as adv, i (adv.adviser_id ?? adv.email ?? i)}
						{@const roleWord = clientTeamRoleLabel(adv.role)}
						<li class="flex items-center justify-between gap-3 py-2">
							<span class="flex min-w-0 items-center gap-2">
								<PersonAvatar name={adv.name ?? adv.email ?? '?'} size="sm" />
								<span class="min-w-0">
									<span class="block truncate font-medium">{adv.name ?? adv.email ?? '—'}</span>
									{#if roleWord}
										<!-- The role on THIS client, from the closed list in `team.ts` — named,
										     never printed raw. -->
										<span class="text-muted-foreground block truncate text-xs">{roleWord}</span>
									{/if}
								</span>
							</span>
							{#if actions?.unassignAdviser && adv.adviser_id}
								<form
									method="POST"
									action={actions.unassignAdviser}
									use:enhanceForm={withToast('Removed from the team', {
										pending: (v) => (removingAdviser = v ? (adv.adviser_id ?? null) : null)
									})}
								>
									<input type="hidden" name="adviser_id" value={adv.adviser_id} />
									<Button
										type="submit"
										variant="ghost"
										size="sm"
										disabled={removingAdviser === adv.adviser_id}
									>
										{removingAdviser === adv.adviser_id ? 'Removing…' : 'Remove'}
									</Button>
								</form>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
			{#if actions?.assignAdviser}
				{#if members.length === 0}
					<p class="text-muted-foreground text-sm">
						Your firm’s people can’t be listed here, so there is nobody to pick from — that read
						needs the members permission.
					</p>
				{:else}
					<!-- The role comes first because it decides who can be picked: Adviser offers only
					     people with the adviser flag, every other role offers the firm's staff. Someone
					     already on the team is offered for a DIFFERENT role (that re-roles them), never
					     for the one they hold. -->
					<form
						method="POST"
						action={actions.assignAdviser}
						use:enhanceForm={withToast('Team updated', {
							pending: (v) => (assigningAdviser = v),
							onSuccess: () => (adviserSel = '')
						})}
						class="grid gap-3 sm:grid-cols-[9rem_minmax(0,1fr)_auto] sm:items-end"
					>
						<div class="space-y-1">
							<Label for="adviser_role">Role</Label>
							<SelectMenu
								id="adviser_role"
								name="role"
								items={CLIENT_TEAM_ROLE_ITEMS}
								value={teamRole}
								onValueChange={pickTeamRole}
							/>
						</div>
						<div class="space-y-1">
							<Label for="adviser_id">Person</Label>
							<SelectMenu
								id="adviser_id"
								name="adviser_id"
								items={teamItems}
								bind:value={adviserSel}
								disabled={teamItems.length === 0}
								placeholder={teamItems.length === 0 ? 'Nobody to pick' : 'Choose a person…'}
							/>
						</div>
						<Button
							type="submit"
							variant="secondary"
							disabled={assigningAdviser || !adviserSel || teamItems.length === 0}
						>
							{assigningAdviser ? 'Assigning…' : 'Assign'}
						</Button>
						{#if teamItems.length === 0}
							<p class="text-muted-foreground text-sm sm:col-span-3">
								{#if teamRole === 'adviser' && !members.some((m) => m.is_adviser)}
									Nobody in your firm is marked as an adviser. Mark staff as advisers on Team, then
									assign them here.
								{:else if eligibleForRole.length === 0}
									{teamRole === 'adviser'
										? 'None of your firm’s advisers currently have active platform access.'
										: 'None of your firm’s staff currently have active platform access.'}
								{:else if teamRole === 'adviser'}
									Nobody else in your firm can be the adviser.
								{:else}
									All of your firm’s staff are already on this team as {teamRoleWord}.
								{/if}
							</p>
						{:else if adviserNote}
							<p class="text-muted-foreground text-sm sm:col-span-3">{adviserNote}</p>
						{/if}
					</form>
				{/if}
			{/if}
		</Dialog.Content>
	</Dialog.Root>
{/if}

<!-- CLIENT LOGINS, behind the key beside the name — the People page's second section. Who can sign
     in as this client, and in what capacity (self, attorney, delegate…). Rendered only when the
     loader supplied `users`, so the key that opens it and the list inside it agree. -->
{#if openLogins && detail.users}
	<Dialog.Root open onOpenChange={(o) => !o && (openLogins = false)}>
		<Dialog.Content class="w-[92%] sm:max-w-lg">
			<Dialog.Title>Profiles</Dialog.Title>
			<p class="text-muted-foreground text-sm">Who can sign in as {name}, and as what.</p>
			{#if membersError}
				<LoadErrorBanner
					message="Your firm’s profiles couldn’t be loaded, so the picker may be incomplete."
					onRetry={onretry}
				/>
			{/if}
			{#if detail.users.length === 0}
				<p class="text-muted-foreground text-xs">No profiles linked.</p>
			{:else}
				<ul class="divide-border divide-y text-xs">
					{#each detail.users as u (u.user_id)}
						<li class="flex items-center justify-between gap-3 py-2">
							<span class="flex min-w-0 items-center gap-2">
								<PersonAvatar name={u.name ?? u.email ?? '?'} size="sm" />
								<span class="min-w-0">
									<span class="block truncate font-medium">{u.name ?? u.email ?? u.user_id}</span>
									<span class="text-muted-foreground block truncate text-xs">
										{CAPACITY_LABELS[u.capacity] ?? u.capacity}
									</span>
								</span>
							</span>
							{#if actions?.unlinkUser}
								<form
									method="POST"
									action={actions.unlinkUser}
									use:enhanceForm={withToast('Profile unlinked', {
										pending: (v) => (removingUser = v ? u.user_id : null)
									})}
								>
									<input type="hidden" name="user_id" value={u.user_id} />
									<Button
										type="submit"
										variant="ghost"
										size="sm"
										disabled={removingUser === u.user_id}
									>
										{removingUser === u.user_id ? 'Removing…' : 'Remove'}
									</Button>
								</form>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
			{#if actions?.linkUser}
				{#if members.length === 0}
					<p class="text-muted-foreground text-sm">
						Your firm’s profiles can’t be listed here, so there is nobody to pick from — that read
						needs the members permission.
					</p>
				{:else if loginItems.length === 0}
					<p class="text-muted-foreground text-sm">
						Nobody in your firm holds client-portal access to link. Grant it on the Team page first.
					</p>
				{:else}
					<form
						method="POST"
						action={actions.linkUser}
						use:enhanceForm={withToast('Profile linked', {
							pending: (v) => (linkingUser = v),
							onSuccess: () => {
								loginSel = '';
							}
						})}
						class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end"
					>
						<div class="space-y-1">
							<Label for="login_user_id">Link profile</Label>
							<SelectMenu
								id="login_user_id"
								name="user_id"
								items={loginItems}
								bind:value={loginSel}
								placeholder="Choose a profile…"
							/>
						</div>
						<Button type="submit" variant="secondary" disabled={linkingUser || !loginSel}>
							{linkingUser ? 'Linking…' : 'Link'}
						</Button>
					</form>
				{/if}
			{/if}
		</Dialog.Content>
	</Dialog.Root>
{/if}

<!-- Vulnerability. Its own drawer rather than fields in the details form, matching the API's own
     split: this is special-category-adjacent data with its own validated endpoint and its own
     audit row, and a details-form validation failure must not be able to half-save it. A sheet,
     not a dialog: the MultiSelect needs vertical room to open, and a centred modal sat on top of
     the record it is describing. FORM DRAWER (`sm:max-w-xl`): a switch, four drivers, a
     MultiSelect and notes do not need the illustration floor. -->
{#if actions?.setVulnerability && openVulnerability}
	<Sheet.Root open onOpenChange={(o) => !o && (openVulnerability = false)}>
		<Sheet.Content side="right" class="gap-0 p-0 sm:max-w-xl">
			<form
				method="POST"
				action={actions.setVulnerability}
				class="flex h-full min-h-0 flex-col"
				use:enhanceForm={withToast(
					(d) =>
						heldLabel(d) ??
						((d as { cleared?: boolean } | undefined)?.cleared
							? 'Vulnerability cleared'
							: 'Vulnerability recorded'),
					{
						pending: (v) => (savingVulnerability = v),
						onSuccess: () => (openVulnerability = false)
					}
				)}
			>
				<Sheet.Header class="border-b px-4 pt-4 pb-3">
					<Sheet.Title>Vulnerability</Sheet.Title>
					<Sheet.Description>
						Record the FCA drivers that apply, and whether it is temporary. The client's profile
						shows this to anyone who opens the record.
					</Sheet.Description>
				</Sheet.Header>

				<!-- The scroller is this wrapper, not `Sheet.Content`: header and footer stay put
				     when the form is taller than the viewport. MultiSelect with `portal={false}`
				     can clip if its panel opens past this box — Command already scrolls its own
				     list, and the picker sits mid-form so there is room below on a typical screen.
				     Same shape as the contribution drawer. -->
				<div class="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain p-4">
					<!-- The flag owns its own row full width, with the consequence stated underneath rather
					     than beside it: the label is `flex items-center`, so a nested description became a
					     third flex item on the same line instead of a block under it. -->
					<div class="space-y-1">
						<div class="flex items-center justify-between gap-4">
							<Label for="vuln-flag" class="m-0 font-medium">This client is vulnerable</Label>
							<Switch
								id="vuln-flag"
								name="is_vulnerable"
								bind:checked={vulnFlag}
								onCheckedChange={(on) => on || clearVulnerabilityDetail()}
							/>
						</div>
						<p class="text-muted-foreground text-xs">
							Turning this off clears the drivers, the duration and the notes.
						</p>
					</div>

					<!-- Everything below describes a vulnerability, so it is disabled AND emptied when the
					     flag is off: a greyed-out driver still ticked would say the record holds something it
					     does not, and the API drops the whole block for a cleared flag anyway. Disabled
					     controls are also not submitted, so the two agree by construction. -->
					<fieldset class="space-y-2" disabled={!vulnFlag} class:opacity-50={!vulnFlag}>
						<legend class="text-sm font-medium">Drivers</legend>
						<p class="text-muted-foreground text-xs">
							More than one usually applies — a bereavement that also leaves someone unable to
							absorb a loss is a life event and a resilience driver.
						</p>
						<div class="grid gap-2 sm:grid-cols-2">
							{#each DRIVER_ENTRIES as [value, label] (value)}
								<label class="flex items-center gap-2 text-sm">
									<Checkbox
										name="reasons"
										{value}
										disabled={!vulnFlag}
										checked={vulnDrivers.includes(value)}
										onCheckedChange={(on) => toggleDriver(value, on)}
									/>
									{label}
								</label>
							{/each}
						</div>
					</fieldset>

					<!-- The CHARACTERISTICS beneath those drivers (FG21/1, 0116).
					
					     MultiSelect, grouped by driver. `portal={false}` because the standing Popover
					     gotcha still holds: a portalled panel sits outside the Sheet's focus trap and
					     never receives a click.
					
					     NOT GATED ON THE DRIVER being ticked. The grouping is presentational (see
					     `CHARACTERISTIC_SELECT_OPTIONS`): if our reading of FG21/1's table were slightly
					     off, gating would make a legitimate characteristic unreachable and only a
					     release could fix it. Every one stays selectable; the headings are labels. -->
					<div class="space-y-1.5" class:opacity-50={!vulnFlag}>
						<Label for="vuln-characteristics">Characteristics</Label>
						<p class="text-muted-foreground text-xs">
							Optional — the driver is the judgement, these are the detail that supports it. Grouped
							as the FCA groups them; any of them can be recorded.
						</p>
						<MultiSelect
							id="vuln-characteristics"
							name="characteristics"
							bind:value={vulnCharacteristics}
							options={CHARACTERISTIC_SELECT_OPTIONS}
							placeholder="Select characteristics"
							searchPlaceholder="Search characteristics…"
							disabled={!vulnFlag}
							portal={false}
						/>
					</div>

					<div class="space-y-1.5" class:opacity-50={!vulnFlag}>
						<!-- Two buttons, and deliberately NOT a required choice: both unset is the API's third
						     state (`is_temporary: null`, "not established"), which is a real answer — a flag
						     raised today often cannot say yet whether it will pass. An unselected control says
						     that better than a "Not established" option pretending to be a choice. The two
						     words also replace the old "Expected to pass?" label, which asked a question they
						     already answer. -->
						<span class="block text-sm font-medium">Duration</span>
						<Segmented
							options={[
								{ value: 'true', label: 'Temporary' },
								{ value: 'false', label: 'Permanent' }
							]}
							disabled={!vulnFlag}
							bind:value={temporaryChoice}
						/>
						<!-- Segmented renders buttons, so the value travels in a hidden field. -->
						<input type="hidden" name="is_temporary" value={temporaryChoice ?? ''} />
						<p class="text-muted-foreground text-xs">
							Leave both unset if it is not yet established.
						</p>
					</div>

					<div class="space-y-1.5" class:opacity-50={!vulnFlag}>
						<Label for="vuln-notes">Notes</Label>
						<Textarea
							id="vuln-notes"
							name="notes"
							rows={3}
							disabled={!vulnFlag}
							bind:value={vulnNotes}
							placeholder="What the adviser needs to know, and any adjustments agreed."
						/>
					</div>
				</div>

				<Sheet.Footer class="flex-row justify-end border-t">
					<Button type="button" variant="ghost" onclick={() => (openVulnerability = false)}>
						Cancel
					</Button>
					<Button type="submit" disabled={savingVulnerability}>
						{savingVulnerability ? 'Saving…' : 'Save'}
					</Button>
				</Sheet.Footer>
			</form>
		</Sheet.Content>
	</Sheet.Root>
{/if}
