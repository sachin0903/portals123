/**
 * The account-book shapes the shared surfaces render.
 *
 * These mirror what `GET /crm/accounts` / `GET /crm/accounts/{id}` return (see apps/api crm.py
 * and each app's `$lib/api`). The components are typed against THESE, and the apps' loader
 * types satisfy them structurally — data stays app-side, the shapes are the contract. If the
 * API grows a field the components render, add it here; the apps' own `$lib/api` types then
 * fail to assign wherever they've drifted, which is the seam doing its job.
 */

import type {
	ClientAddress,
	VulnerabilityCharacteristic,
	VulnerabilityReason
} from '../clients/types.js';

/** The owning client as nested by GET /crm/accounts — the shape `clientName` formats. */
export type AccountClient = {
	id: number | null;
	uid: string | null;
	type: 'individual' | 'organisation';
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
	address?: ClientAddress | null;
	relevant_uk_earnings?: number | null;
	pension_protection?: string[] | null;
	flexi_access_taken?: boolean | null;
	/** The owner's vulnerability (0054), stated on the account header as well as on their record:
	 *  this page is a place their money is moved, and the flag governs how that conversation is
	 *  conducted. Optional and only populated by the account DETAIL endpoint — the listing omits
	 *  it, so `undefined` means "not fetched" and must not draw the flag. */
	is_vulnerable?: boolean | null;
	vulnerability_reasons?: VulnerabilityReason[] | null;
	vulnerability_characteristics?: VulnerabilityCharacteristic[] | null;
	vulnerability_is_temporary?: boolean | null;
	vulnerability_recorded_at?: string | null;
	vulnerability_notes?: string | null;
};

/** A row of the accounts listing — GET /crm/accounts, RLS-scoped. */
export type Account = {
	id: number;
	/** The account's address. NOT NULL since `0111_accounts_uid_not_null` — an account without
	 *  one could not be linked to. */
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
	/** The shelf item's own CODE (`platform.products.code`), beside its name.
	 *
	 *  Surfaced because it is how a bad product mapping becomes visible. Every account surface
	 *  rendered `product_name ?? product_type`, which collapses three different states into one
	 *  reading: an account whose product parent resolved correctly, one that resolved to the WRONG
	 *  shelf item, and one that resolved to NOTHING and is falling back to the raw provider enum.
	 *  Naming the product the account is actually LINKED to tells them apart — and on the dev book
	 *  98 of 379 accounts have no product parent at all, several of them named "SIPP". */
	product_code?: string | null;
	product_type: string | null;
	/** Who HOLDS the assets (`platform.accounts.custodian`) — the provider code, not the display
	 *  name. Distinct from `scheme_administrator`, who runs the scheme. */
	custodian?: string | null;
	/** Whether the wrapper is a pension, from `platform.products.is_pension`. Optional because
	 *  the listing carried `product_type` alone before the column had a reader. */
	is_pension?: boolean;
	/** Which provider administers the scheme. An SEI SIPP is administered by Morgan Lloyd. A
	 *  Seccl SIPP is administered by Seccl. Null on a GIA or an ISA, and on a pension whose
	 *  shelf item names no administrator.
	 *
	 *  It is what decides whether an account has a member journey at all: a custodian publishes
	 *  none, so drawdown is an administrator's act. */
	scheme_administrator?: string | null;
	/** Display name for `scheme_administrator`, from the same registry as `platform_name`. */
	scheme_administrator_name?: string | null;
	/** Managed against a discretionary model (i.e. the account carries a model_id). */
	discretionary: boolean;
	/**
	 * The service the firm provides on this account — `advisory` | `discretionary` |
	 * `execution_only`, or null when nothing we hold says.
	 *
	 * Decided by the API, never here (`serviceTypeLabel` only labels it): the listing filters on
	 * this same field server-side, so a second derivation would disagree with the filter.
	 */
	service_type: string | null;
	/** Stable catalogue identity used by the model filter; the name is presentation only. */
	model_id: number | null;
	model_name: string | null;
	adviser: string | null;
	client: AccountClient;
	/** Cash at the CUSTODIAN, derived from the lake's positions. Not the whole story for a
	 *  pension — see `provider_positions`. */
	cash: number;
	growth: number;
	/** Cash at the SCHEME ADMINISTRATOR, and a crystallisation split.
	 *
	 *  Administrator rows carry the scheme's bank balance (summed with `cash`). Custodian rows
	 *  carry a crystallisation only, and only when the administrator has not stated one — their
	 *  cash is null so the lake figure is not added twice. Empty when there is nothing further
	 *  to say about where the money sits. */
	provider_positions?: ProviderPosition[] | null;
};

/** One scheme administrator's latest position for an account (0117). */
export type ProviderPosition = {
	/** The provider key, e.g. `morgan_lloyd`. */
	source: string;
	/** The scheme's own bank balance. CAN BE NEGATIVE — the vendor says so, and an overdrawn
	 *  scheme is something an adviser has to see rather than something to floor at zero. */
	cash: number | null;
	as_of: string | null;
	crystallised: number | null;
	uncrystallised: number | null;
};

/** An authoritative option supplied by the app from the tenant's enabled catalogue. */
export type AccountFilterOption = {
	value: string;
	label: string;
};

/** Bank details for a client (crm.contacts.bank_account jsonb) — one set per person.
 *  UK (BACS) rows carry sort_code + account_number; international rows carry iban + bic. */
export type BankAccount = {
	name?: string | null;
	sort_code?: string | null;
	account_number?: string | null;
	bank?: string | null;
	iban?: string | null;
	bic?: string | null;
};

/** SEI's own withdrawal rail fees — a deployment-wide setting (`app/config.py`), the same for
 *  every firm, so it travels with the account read rather than needing its own endpoint. BACS
 *  carries none, which is why it has no key here. */
export type WithdrawalFees = {
	faster_payment: number;
	swift: number;
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

/**
 * One equal fee-package assignment, ordered for presentation only.
 *
 * The charge travels with it (`GET /crm/accounts/{uid}` projects the catalogue row's own pricing
 * fields), because the card used to list the firm's pricing LABEL and nothing else — "Advised
 * standard", with neither the figure nor how often it is taken. Every field past `sort_order` is
 * optional so an older API build still renders the name alone.
 */
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

/**
 * GET /crm/accounts/{id} — everything the account detail surface renders.
 * (`AccountDetailData`, not `AccountDetail`: that name is the detail COMPONENT.)
 */
export type AccountDetailData = Omit<Account, 'client'> & {
	closed_at: string | null;
	product_id: number | null;
	platform_identifier: string | null;
	platform_name: string | null;
	fee_packages: AccountFeePackage[];
	/** Whether this product may hold a withdrawal destination. False for products such as JISAs.
	 * Optional only for the rolling-deploy window against an API image from before migration 0167. */
	bank_details_allowed?: boolean;
	third_party_pension_provider: string | null;
	fees: AccountFee[] | null;
	payments: RegularMovement[] | null;
	withdrawals: RegularMovement[] | null;
	transfer: Record<string, unknown> | null;
	client: AccountClient & { bank_account: BankAccount | null };
	/**
	 * This account's own bank accounts (`platform.accounts.bank_accounts`). `[0]` is the
	 * default a payment takes. Empty / null → the card falls back to `client.bank_account`
	 * and says so. Resolution is account first, client second (`app/bank_accounts.py`).
	 */
	bank_accounts: BankAccount[] | null;
	/** Optional so an older API / a mount that never fetched it degrades to the withdrawal
	 *  drawer's own built-in defaults rather than failing to render. */
	withdrawal_fees?: WithdrawalFees | null;
	/** ISA/JISA subscription, as the custodian states it. Absent on every other wrapper. */
	allowance_used?: number | null;
	allowance_limit?: number | null;
	allowance_remaining?: number | null;
	allowance_tax_year?: string | null;
};

export type FeePackageOption = {
	id: number;
	name: string;
	description?: string | null;
	fee_type?: string | null;
	frequency?: string | null;
	charge_basis?: string | null;
	payer?: string | null;
	currency?: string | null;
	vatable?: boolean | null;
	minimum_amount?: number | null;
	maximum_amount?: number | null;
	rule?: unknown;
	/** The firm's own grouping — 'adviser', 'platform', 'unclassified' … Free text, because it is
	 *  the provider's or the firm's word and not a vocabulary we own; printed through `humanise`. */
	category?: string | null;
};

export type AccountContributionSummary = {
	taxYear: string;
	accountGross: number;
	count: number;
};

/**
 * Route knowledge, injected. The shared components never hardcode an href — the platform and
 * admin portals mount the same surfaces on different route trees, so every link is a builder
 * the app supplies. Optional builders are ACTION entry points: absent ⇒ the affordance is
 * hidden and the surface reads as read-only.
 *
 * Every builder takes a **uid**, never the serial `id`. A URL is published — it is bookmarked,
 * pasted into a ticket and read out loud — and a serial says how many rows preceded it, which is
 * not a thing to publish about a firm's book. The rows carry both: the id is what the API's
 * filters and joins key on, the uid is the address.
 */
/** One beneficiary nomination on a pension account (`crm.beneficiaries`, 0127).
 *
 *  `proportion` is a percentage of the fund (`cam_percentageoffund`), not an amount: a share stays
 *  right when the fund's value changes, where an amount silently stops being what the member
 *  meant. Typed `number` and read defensively — the column is `numeric(6,3)`, and a numeric can
 *  reach a JSON boundary as a string. */
export interface Beneficiary {
	uid: string;
	title?: string | null;
	name: string;
	relationship?: string | null;
	proportion: number;
	nominated_on?: string | null;
}

export interface AccountBookLinks {
	/** An account's detail page — the listing rows and the transactions' account column. */
	account: (uid: string) => string;
	/** An account's transaction band, used to clear an instrument-focused ledger. */
	accountTransactions?: (uid: string) => string;
	/** A client's record — wherever an owner is named. */
	client: (uid: string) => string;
	/** A pending review on this account — opens the Reviews queue on that case. */
	review?: (uid: string) => string;
	/**
	 * The new-transfer entry point for an account (platform: `/transfers/new?account={uid}`).
	 * Absent ⇒ the "New transfer" button is hidden (e.g. an admin read-only view).
	 */
	newTransfer?: (accountUid: string) => string;
}

/** Which band of the account record to render.
 *
 * These were tabs inside `AccountDetail`; they are ROUTES now, and the record's sidebar is the
 * strip — the same shape the client record already had. The component renders the header on every
 * section, because the value/growth/cash figures and the money actions are what an adviser needs
 * whichever band they are on.
 */
export type AccountSection = 'overview' | 'holdings' | 'transactions';

/** What `AccountDetail` will render.
 *
 * `all` stacks the three bands on one page and is the DEFAULT, because a mount that does not route
 * the sections must not silently lose two thirds of the record — which is exactly what defaulting
 * to `overview` did to the admin portal's read-only mount. An app that owns routes for the bands
 * (the platform) passes one; an app that does not (admin ops) passes nothing and gets all of it.
 */
export type AccountView = AccountSection | 'all';
