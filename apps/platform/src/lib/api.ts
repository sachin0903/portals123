import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { ScreeningCheck as KycCheck } from '@graphene/ui';
import type {
	BookEmploymentStatus,
	BookGender,
	BookMaritalStatus,
	ProviderPosition,
	TaxRelief,
	VulnerabilityCharacteristic,
	WithdrawalFees
} from '@graphene/ui';

export type { KycCheck };

/** Base URL of the FastAPI backend (server-side calls). */
export const apiUrl = (): string => env.API_URL ?? 'http://127.0.0.1:8000';

/** Bearer auth header for the signed-in user's access token. */
export const authHeaders = (accessToken: string | null): Record<string, string> => ({
	Authorization: `Bearer ${accessToken}`
});

export const jsonHeaders = (accessToken: string | null): Record<string, string> => ({
	...authHeaders(accessToken),
	'Content-Type': 'application/json'
});

/** Result of a load()-time GET: parsed data on success, or the failing status so the caller can
 * surface a retry banner instead of substituting fake-empty data. */
export type LoadResult<T> = { ok: true; data: T } | { ok: false; status: number };

/** Backoff (ms) before each automatic retry; its length sets the retry count (here: 2 retries,
 * so 3 attempts total). */
const RETRY_BACKOFF_MS = [150, 400];

/** Transient statuses worth retrying: a network error (status 0) or a 401 — the last is
 * usually the token-refresh boundary but can also be a momentary JWKS/verification hiccup on the
 * API (see apps/api/app/auth.py), which a short retry clears. 5xx fail fast: retrying a hanging
 * or overloaded API on a page load just makes the click feel slower. Deterministic 4xx
 * (403/404/400/422) fail fast too. */
const isTransient = (status: number): boolean => status === 0 || status === 401;

const FETCH_TIMEOUT_MS = 12_000;

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** GET + parse JSON for a page load, distinguishing a genuine failure from a legitimately empty
 * result. Retries transient failures a couple of times with a short backoff before giving up; the
 * caller surfaces a Retry banner on persistent failure (its Retry re-runs load through a fresh
 * request, where hooks.server.ts refreshes an expired token). Never collapses a non-OK response
 * to an empty array (the old `res.ok ? json : []` masked transient failures as "no data"). */
export async function fetchJson<T>(
	fetch: typeof globalThis.fetch,
	url: string,
	headers: Record<string, string>
): Promise<LoadResult<T>> {
	const attempts = RETRY_BACKOFF_MS.length + 1;
	for (let attempt = 0; attempt < attempts; attempt++) {
		if (attempt > 0) await delay(RETRY_BACKOFF_MS[attempt - 1]);
		const last = attempt === attempts - 1;
		try {
			const res = await fetch(url, { headers, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
			if (res.ok) return { ok: true, data: (await res.json()) as T };
			if (!isTransient(res.status) || last) {
				console.error(`load fetch failed: ${url} → ${res.status}`);
				return { ok: false, status: res.status };
			}
			console.warn(
				`load fetch ${res.status} on ${url} — retrying (attempt ${attempt + 1}/${attempts})`
			);
		} catch (err) {
			if (last) {
				console.error(`load fetch errored: ${url}`, err);
				return { ok: false, status: 0 };
			}
			console.warn(`load fetch errored on ${url} — retrying (attempt ${attempt + 1}/${attempts})`);
		}
	}
	return { ok: false, status: 0 };
}

/**
 * A client's serial id, for the one thing that still needs one: a request BODY.
 *
 * Every PATH takes the uid now, so nothing resolves to build a URL — this exists because
 * `POST /documents/requests` carries `contact_id` in its body, and a body is not a URL. It reads
 * the record at its uid and keeps the id; an unknown uid 404s inside the caller's own RLS session,
 * exactly as an out-of-scope id would.
 */
export async function contactIdFrom(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	uid: string
): Promise<number> {
	const res = await fetch(`${apiUrl()}/crm/contacts/${uid}`, { headers: authHeaders(accessToken) });
	if (!res.ok) error(res.status === 404 ? 404 : res.status, await detail(res));
	const body = (await res.json()) as { contact?: { id: number } };
	const id = body.contact?.id;
	if (typeof id !== 'number') error(500, 'Record resolved without an id');
	return id;
}

/** The account counterpart, for the same reason: `POST /contributions` and `POST /transfers`
 *  carry `account_id` in their bodies. Every PATH takes the uid. */
export async function accountIdFrom(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	uid: string
): Promise<number> {
	const res = await fetch(`${apiUrl()}/crm/accounts/${uid}`, { headers: authHeaders(accessToken) });
	if (!res.ok) error(res.status === 404 ? 404 : res.status, await detail(res));
	const body = (await res.json()) as { id?: number };
	if (typeof body.id !== 'number') error(500, 'Record resolved without an id');
	return body.id;
}

/** Pull a human-readable message out of a FastAPI error response. */
export async function detail(res: Response): Promise<string> {
	try {
		const body = await res.json();
		if (typeof body?.detail === 'string') return body.detail;
		if (Array.isArray(body?.detail) && body.detail[0]?.msg) return String(body.detail[0].msg);
		// A structured refusal (`{message, ...}` — the live-journey 409, an automation that
		// cannot arm) still reads as its sentence here; callers that want the rest parse the body.
		if (typeof body?.detail?.message === 'string') return body.detail.message;
	} catch {
		/* fall through */
	}
	return `The request failed (${res.status}). Please try again.`;
}

/** The two portals a firm manages access to (operations is Graphene-internal). */
export type Portal = 'client' | 'platform';
export type AccessKind = 'staff' | 'profile' | 'unclassified';
export type UserType = 'client' | 'staff';

/** One portal a person holds within the firm. */
export type MemberPortal = {
	portal: Portal;
	/** Complete permission-bearing role set. role_slug is the stable primary/cosmetic role. */
	role_slugs: string[];
	role_slug: string | null;
	status: string;
	created_at: string;
};

/** One custodian that knows a person, NAMED BY THE API (`providers.meta()`). The portals keep no
 *  table of provider names: six hard-coded copies had already drifted, and the one that mattered
 *  fell back to Morgan Lloyd's name for any unlisted provider, so every SEI surface carried the
 *  wrong custodian's. Unnamed beats misnamed, decided server-side. */
export type MemberProvider = {
	provider: string;
	name: string;
};

export type Member = {
	user_id: string;
	/** Public auth-profile handle used in portal URLs. */
	user_uid: string;
	email: string;
	name: string | null;
	enabled: boolean;
	avatar: string | null;
	created_at: string;
	last_sign_in: string | null;
	invitation_pending: boolean;
	invitation_expires_at: string | null;
	/** Canonical identity sides. Both values occur only on grandfathered identities awaiting split. */
	types: UserType[];
	/** Same array as `types`, under the column's own name. Either may be absent on an older payload. */
	type?: string[];
	/** Transitional response compatibility; prefer `types`. */
	access_kind: AccessKind;
	portals: MemberPortal[];
	/** The custodians this person is known to — `auth.users.provider_refs` (0118). Empty for
	 *  someone no custodian has been mapped to, which is the ordinary state. */
	providers: MemberProvider[];
	/** True when the ROW came from a custodian's own directory rather than from WorkOS: a
	 *  placeholder `auth.merge_provider_user` (0123) minted, disabled and holding no portal. False
	 *  for a real staff login even when it carries a provider id. */
	from_provider: boolean;
	/** Whether this member of staff may hold a client book (0125). A boolean and not a `type`
	 *  member because advising is a capability WITHIN staff — `{client, adviser}` is not a person.
	 *  No custodian can tell us, so an operator states it. */
	is_adviser: boolean;
	/** The firm's own words about them (0118), as the team member page edits them. */
	first_name: string | null;
	last_name: string | null;
	direct_phone: string | null;
	job_title: string | null;
	qualifications: string[];
	fca_reference: string | null;
	bio: string | null;
	photo_url: string | null;
};

export type OrgRole = {
	slug: string;
	name: string;
	description: string | null;
	portal: Portal | null;
	permissions: string[];
};

export type Permission = {
	slug: string;
	name: string;
	resource: string | null;
	action: string | null;
	portal: Portal | null;
	description: string | null;
};

/** A client (a `crm.contacts` row — the client *is* the contact; there is no separate
 *  client entity) as returned by GET /crm/contacts, RLS-scoped to what the caller may see. */
export type Client = {
	id: number;
	uid: string;
	type: 'individual' | 'organisation';
	service_type: 'advised' | 'execution_only' | null;
	enabled: boolean;
	deleted_at?: string | null;
	provider_status?: string | null;
	/** Base currency (ISO 4217). GBP when the custodian does not publish one. */
	currency?: string | null;
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
	relevant_uk_earnings?: number | null;
	pension_protection?: string[] | null;
	email: string | null;
	/** The mobile. Required: the listing and the record both return it. */
	mobile: string | null;
	/** The other number. Null and absent both mean none. */
	phone?: string | null;
	address: Address | null;
	portfolio_value: number;
	account_count: number;
	/** JISA accounts this person represents but does not own; excluded from their AUM. */
	represented_account_count?: number;
	/** The distinct wrappers the client holds (`GIA`, `ISA`, `SIPP`…), sorted by the API — what the
	 *  listing shows in place of the bare count. */
	account_types: string[];
	/** The client's kind at its most specific: `individual`, or the organisation's `sub_type`
	 *  (`trust`/`company`/`charity`) falling back to `organisation`. Decided by the API. */
	client_type: string | null;
	/** An organisation's kind, as stored. Returned by the RECORD endpoint; the listing publishes
	 *  the collapsed `client_type` above instead. */
	sub_type?: string | null;
	advisers: string[];
	/** Cash held across the client's accounts, from the lake. Null until that read lands
	 *  (or when it gives up); 0 when it was read and nothing is held. */
	cash: number | null;
	/** Unrealised gain across the client's holdings, from the lake. Same convention as `cash`. */
	growth: number | null;

	/** The pension and identity facts (migrations 0052-0053, narrowed by 0235), returned by
	 *  GET /crm/contacts/{id}. Optional for the same reason as the vulnerability block: the
	 *  listing endpoint omits them. Gender, marital status and employment status are the shared
	 *  field-type lists (`@graphene/ui` vocabularies). */
	gender?: BookGender | null;
	marital_status?: BookMaritalStatus | null;
	employment_status?: BookEmploymentStatus | null;
	/** Intended retirement age, 50-75. Default for an illustration's exit age. */
	retirement_age?: number | null;
	source_of_wealth?: string | null;
	/** Taking flexible benefits triggers the £10,000 money purchase annual allowance. */
	flexi_access_taken?: boolean | null;

	/** The KYC answers onboarding always collected and had nowhere to put (0116) — they folded
	 *  into `data` and reached the record only as a dotted path under the generic overflow.
	 *  `occupation` is NOT `employment_status`: what they do vs whether they work. */
	occupation?: string | null;
	occupation_detail?: string | null;
	country_of_birth?: string | null;
	town_of_birth?: string | null;
	/** Where THIS payment came from (JMLSG), distinct from source_of_wealth's lifetime narrative. */
	source_of_funds?: string | null;
	source_of_funds_detail?: string | null;
	/** The member's own tax-relief declaration (ML cam_taxrelief). Never derived from earnings. */
	tax_relief?: TaxRelief | null;

	/** Vulnerability (FCA FG21/1), returned by GET /crm/contacts/{id}. Optional because the
	 *  listing endpoint does not carry it — only the detail read does. */
	is_vulnerable?: boolean | null;
	/** The drivers that apply: `health` | `life_event` | `resilience` | `capability`. An array
	 *  because they compound; the API rejects anything outside that set. */
	vulnerability_reasons?: VulnerabilityReason[] | null;
	/** The FG21/1 characteristics beneath those drivers (0116). Optional where a driver is
	 *  required: a flag raised in a first conversation often has the driver before the specifics. */
	vulnerability_characteristics?: VulnerabilityCharacteristic[] | null;
	/** Whether it is expected to pass. `null` is a real third state: not established. */
	vulnerability_is_temporary?: boolean | null;
	/** When the flag was last set, so a temporary vulnerability has a review point. */
	vulnerability_recorded_at?: string | null;
	vulnerability_notes?: string | null;
};

/** The FCA's four drivers of vulnerability (FG21/1). Kept in step with
 *  `app/contacts.py::VULNERABILITY_REASONS` — the API is the authority and rejects the rest. */
export type VulnerabilityReason = 'health' | 'life_event' | 'resilience' | 'capability';

/** The 24 FG21/1 characteristics beneath those drivers, and the tax-relief declaration (0116).
 *
 *  RE-EXPORTED FROM `@graphene/ui`, not re-declared. The three unions above are app-local copies
 *  of vocabularies that also live in `pkgs/ui` and in `app/contacts.py`, held together by pin
 *  tests — which is workable for four members and a bad trade for twenty-four: a fourth
 *  hand-written copy of the characteristics is a fourth thing to keep in step, and a slug spelled
 *  differently here would typecheck against nothing and fail only at the API.
 *
 *  From the ROOT `@graphene/ui`, not a `book/clients` subpath: `book/clients` is not in the
 *  package's export map (unlike `book/tasks` and friends) — its index is re-exported through
 *  `src/index.ts`, which is how `ScreeningCheck` already arrives here. */
export type { VulnerabilityCharacteristic, TaxRelief } from '@graphene/ui';

/** The owning client as nested by GET /crm/accounts — the shape `clientName` formats. */
export type AccountClient = {
	id: number | null;
	uid: string | null;
	type: 'individual' | 'organisation';
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
	address?: Address | null;
	relevant_uk_earnings?: number | null;
	pension_protection?: string[] | null;
	flexi_access_taken?: boolean | null;
};

/** A row of the accounts listing — GET /crm/accounts, RLS-scoped. */
export type Account = {
	id: number;
	/** NOT NULL since `0111_accounts_uid_not_null` — it is the account's address. */
	uid: string;
	name: string | null;
	status: string;
	currency: string | null;
	value: number | null;
	contact_id: number | null;
	risk_level: string | null;
	custody: string | null;
	opened_at: string | null;
	product_name: string | null;
	product_type: string | null;
	/** Who HOLDS the assets (`platform.accounts.custodian`) — the provider code. Distinct from
	 *  `scheme_administrator`. */
	custodian?: string | null;
	/** The shelf's own answer to "is this a pension" (`platform.products.is_pension`), which beats
	 *  the nine disagreeing hardcoded wrapper sets. */
	is_pension?: boolean;
	/** Which provider ADMINISTERS the scheme, where `custodian` holds the assets — different
	 *  counterparties, and for this estate's SIPP different companies. Null for a GIA or an ISA,
	 *  and for any pension whose shelf item names no administrator. */
	scheme_administrator?: string | null;
	/** Managed against a discretionary model (i.e. the account carries a model_id). */
	discretionary: boolean;
	/** The service the firm provides on this account — `advisory` | `discretionary` |
	 *  `execution_only`, or null when nothing we hold says. Decided by the API. */
	service_type: string | null;
	model_id: number | null;
	model_name: string | null;
	adviser: string | null;
	client: AccountClient;
	cash: number;
	growth: number;
};

/** Bank details for a client (crm.contacts.bank_account jsonb) — one set per person. */
export type BankAccount = {
	name?: string | null;
	sort_code?: string | null;
	account_number?: string | null;
	bank?: string | null;
	iban?: string | null;
	bic?: string | null;
	/** UK rail saved as the default. Absent means BACS. */
	method?: string | null;
};

/** A scheduled money movement on an account (platform.accounts.payments/withdrawals jsonb). */
export type RegularMovement = {
	amount?: number | null;
	frequency?: string | null;
	next_date?: string | null;
	method?: string | null;
	reference?: string | null;
};

/** A fee line on an account (platform.accounts.fees jsonb). */
export type AccountFee = {
	name?: string | null;
	type?: string | null;
	frequency?: string | null;
	rate?: number | null;
	amount?: number | null;
};

export type AccountFeePackage = {
	id: number;
	name: string;
	sort_order: number;
	fee_type?: string | null;
	frequency?: string | null;
	charge_basis?: string | null;
	payer?: string | null;
	currency?: string | null;
	description?: string | null;
	vatable?: boolean | null;
	minimum_amount?: number | null;
	maximum_amount?: number | null;
	rule?: unknown;
};

/** GET /crm/accounts/{id} — everything the account detail page renders. */
export type AccountDetail = Omit<Account, 'client'> & {
	closed_at: string | null;
	product_id: number | null;
	platform_identifier: string | null;
	platform_name: string | null;
	/** The custodian's own account number (SEI's, for a SEI account): the reference a client
	 *  quotes on a pay-in so the credit reconciles. Null until the custodian has opened it. */
	custodian_account_number: string | null;
	/** The custodian's collection bank account — where clients pay money in — from the firm's
	 *  platform settings. Shown in the account's payment drawer. Null until configured. */
	pay_in_account_name: string | null;
	pay_in_sort_code: string | null;
	pay_in_account_number: string | null;
	fee_packages: AccountFeePackage[];
	/** Optional only while a newly-deployed portal may still be reading the pre-0167 API image. */
	bank_details_allowed?: boolean;
	third_party_pension_provider: string | null;
	fees: AccountFee[] | null;
	payments: RegularMovement[] | null;
	withdrawals: RegularMovement[] | null;
	transfer: Record<string, unknown> | null;
	/** Cash at the scheme administrator, and a crystallisation split.
	 *
	 *  Administrator rows carry the scheme bank balance, which is summed with the custodian's
	 *  lake cash — SEI does not know about money passed to Morgan Lloyd. Custodian rows carry
	 *  crystallised/uncrystallised only, and only when the administrator has not stated a split;
	 *  their cash is null so the lake figure is not counted twice. `market_value` is not
	 *  returned: the administrator's fund figure is the valuation we pushed them. */
	provider_positions: ProviderPosition[];
	/** ISA/JISA subscription, as the custodian states it. Null on every other wrapper. */
	allowance_used?: number | null;
	allowance_limit?: number | null;
	allowance_remaining?: number | null;
	allowance_tax_year?: string | null;
	/**
	 * This account's own bank accounts (`platform.accounts.bank_accounts`). `[0]` is the
	 * default. Empty / null → the card falls back to `client.bank_account`. The vulnerability
	 * block is the owner's and is populated only here, not on the listing.
	 */
	bank_accounts: BankAccount[] | null;
	/** SEI's own withdrawal rail fees (`app/config.py`, same for every firm) — the withdrawal
	 *  drawer's own defaults stand in when this is absent. */
	withdrawal_fees?: WithdrawalFees | null;
	client: AccountClient & {
		bank_account: BankAccount | null;
		is_vulnerable: boolean | null;
		vulnerability_reasons: VulnerabilityReason[] | null;
		vulnerability_is_temporary: boolean | null;
		vulnerability_recorded_at: string | null;
		vulnerability_notes: string | null;
	};
};

/** One scheme administrator's latest position for an account (0117). Re-exported from
 *  `@graphene/ui` rather than re-declared, the posture `VulnerabilityCharacteristic` takes. */
export type { ProviderPosition } from '@graphene/ui';

/** A client's postal address (crm.contacts.address jsonb). All parts optional. */
export type Address = {
	line1?: string | null;
	line2?: string | null;
	city?: string | null;
	postcode?: string | null;
	country?: string | null;
};

/** An account owned by a client, as returned inside the contact detail / GET /crm/accounts. */
export type ClientAccount = {
	id: number;
	/** NOT NULL since `0111_accounts_uid_not_null` — it is the account's address. */
	uid: string;
	name: string | null;
	status: string;
	value: number | null;
	product_name: string | null;
	product_type: string | null;
	/** The service the firm provides on the account — the same field and the same SQL expression
	 *  the accounts listing reads (`crm.py`), labelled by `serviceTypeLabel`. */
	service_type: string | null;
	/** From `platform.products.is_pension` — what shows the record's Pension and Employer
	 *  sections. False for an account whose product parent has not resolved. */
	is_pension: boolean;
	model_id: number | null;
	model_name: string | null;
	cash: number;
	growth: number;
	/** This account's own pay-to details. Empty or absent means it uses the client's. */
	bank_accounts?: BankAccount[] | null;
	/** SEI's rail tariff, or null when this custodian does not charge one. */
	withdrawal_fees?: WithdrawalFees | null;
};

/** A money-movement instruction (demo-grade) — GET/POST /firm/contacts/{id}/instructions. */
export type InstructionKind =
	'expected_in' | 'withdrawal' | 'adhoc_fee' | 'model_topup' | 'model_withdrawal';
export type Instruction = {
	uid: string;
	kind: InstructionKind;
	amount: number;
	rail_fee?: number | null;
	payment_method?: string | null;
	/** When false the firm pays the rail fee. Absent means it is passed on. */
	pass_rail_fee?: boolean;
	status: 'pending' | 'settled' | 'cancelled';
	due_date: string | null;
	note: string | null;
	created_at: string;
	/** Which account the instruction is a claim against — a *pending* row still promises that
	 *  account's money away, so the screen's "available" figure has to net them off. */
	account_id: number;
	/** The account's uid — what an href into the account record is built from. The serial above is
	 *  what the API's own filters and joins key on; a URL never carries it. Published by
	 *  `instructions.py`'s projection all along; the type had simply never named it. */
	account_uid: string;
	account_name: string | null;
	model_name: string | null;
};

export type ContactAdviser = {
	adviser_id: string;
	role: string | null;
	name: string | null;
	email: string | null;
	job_title?: string | null;
	is_adviser?: boolean;
};

export type ContactUser = {
	user_id: string;
	capacity: string;
	name: string | null;
	email: string | null;
};

/**
 * `GET /crm/contacts/{uid}/shell` — the chrome every section of the record needs (identity,
 * accounts, team, KYC status, this client's unread flag) without the fat person record.
 * Overview and Details fetch `GET /crm/contacts/{uid}` themselves for bank / FATCA / notes.
 */
export type ContactShell = {
	contact: Pick<Client, 'id' | 'uid'> &
		Partial<Client> & {
			name?: string | null;
			first_name?: string | null;
			surname?: string | null;
			title?: string | null;
			email?: string | null;
			deleted_at?: string | null;
			is_vulnerable?: boolean | null;
		};
	accounts: Array<
		Pick<ClientAccount, 'id' | 'uid'> &
			Partial<Pick<ClientAccount, 'name' | 'value' | 'product_type' | 'is_pension'>>
	>;
	advisers?: ContactAdviser[];
	users?: ContactUser[];
	kyc?: KycCheck[];
	chats_unread?: boolean;
};

/** Lift a shell into the `ContactDetail` shape children already read via `parent()`. Fat fields
 *  that the shell does not carry are empty, not guessed — a tab that needs them fetches the
 *  record itself. */
export function detailFromShell(shell: ContactShell): ContactDetail {
	return {
		contact: shell.contact as Client,
		advisers: shell.advisers ?? [],
		users: shell.users ?? [],
		accounts: (shell.accounts ?? []) as ContactDetail['accounts'],
		notes: [],
		notes_total: 0,
		kyc: shell.kyc ?? [],
		relationships: [],
		value_source: null
	};
}

/** GET /crm/contacts/{id} — one client with their advisers, linked logins and accounts.
 *  `kyc`, `relationships`, `notes_total` and `value_source` were all being returned by the endpoint
 *  and missing from this type, so the payload was wider than the contract said. */
export type ContactDetail = {
	contact: Client;
	advisers: ContactAdviser[];
	users: ContactUser[];
	accounts: ClientAccount[];
	notes: ClientNote[];
	notes_total: number;
	/** Latest identity/AML check per kind (crm.kyc_checks). Adviser-facing only — see @graphene/ui (book/clients/screening). */
	kyc: KycCheck[];
	term_acceptances?: {
		uid: string;
		term_uid: string;
		title: string;
		code: string;
		version_number: number;
		version_uid: string;
		accepted_at: string;
		channel: string;
	}[];
	relationships: ContactRelationship[];
	value_source: string | null;
};

/** A party linked to this client from either end of crm.relationships. */
export type ContactRelationship = {
	id: number;
	relationship_type: string;
	outbound: boolean;
	other_contact_id: number;
	other_type: 'individual' | 'organisation';
	other_name: string | null;
	other_title: string | null;
	other_first_name: string | null;
	other_surname: string | null;
	other_uid: string;
};

export type ClientNote = {
	id: number;
	body: string;
	author: string | null;
	created_at: string;
};

/** A client as summarised on the dashboard's top-clients list. */
export type OverviewClient = {
	id: number;
	type: 'individual' | 'organisation';
	name: string | null;
	first_name: string | null;
	surname: string | null;
	service_type: 'advised' | 'execution_only' | null;
	aum: number;
	account_count: number;
	adviser: string | null;
};

/** A point on the firm value series (label = date, value = £). */
export type SeriesPoint = { label: string; value: number };

/** GET /crm/overview — firm KPIs for the dashboard. `top` is retired: the page paints the
 *  real book table, not a six-row teaser. */
export type Overview = {
	clients: number;
	accounts: number;
	aum: number;
	advised: number;
	execution_only: number;
	clients_funded: number;
	gain: number;
	gainPct: number;
	series: SeriesPoint[];
	top: OverviewClient[];
};

// Pure presentation helpers moved to `$lib/format` (client-safe): this module imports
// server-only `$env/dynamic/private`, so components must not import values from here.
