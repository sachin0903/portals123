import { describeFeeRule } from '@graphene/ui/book/journeys';

/** Shapes returned by `GET /admin/tenants/{id}/catalogue` (app/routers/platform_catalogue.py).
 *
 * Hand-written rather than taken from `@graphene/api-client`: the endpoints return
 * `dict[str, Any]`, so the generated client types them as `unknown` and every field access
 * would need a cast. These are the columns the page actually reads.
 */

export type PlatformRef = {
	id?: number;
	/** THE PROVIDER CODE, and it is the whole identity (0131). `platform.platforms` was dropped, so
	 *  there is no `platform_id` surrogate and no joined `platform_name` — the display name comes
	 *  from the platform registry the API also serves as `catalogue.platforms`. */
	provider: string;
	external_id: string | null;
	external_name: string | null;
	/** The reference a write SENDS to open an account (SEI `accountTemplateId`, Seccl
	 *  `schemeProductId`). Distinct from `external_id`, which is the reference the pipeline
	 *  READS. Set from the product settings sheet. */
	account_open_reference: string | null;
	/** What this provider DOES for the product (0122): custodian, scheme administrator or wrapper
	 *  provider. Stamped by the pipeline from the provider's own declared role. */
	role: 'custodian' | 'scheme_administrator' | 'wrapper_provider' | null;
	enabled: boolean;
};

/** One row from `GET /admin/providers/sei/options` — firms, subfirms, DSR teams, enrolment
 *  templates. `parent` is the owning firm; the products page keeps only the tenant's enabled
 *  SEI firm (`enrollmentTemplatesForFirm`). */
export type ProviderOption = {
	value: string;
	label: string;
	hint: string | null;
	parent: string | null;
};

export type Product = {
	id: number;
	/** The row's address — `/catalogue/…/{uid}` takes it; the id is what the
	 *  payload's own joins key on. */
	uid: string;
	product_type: string;
	name: string;
	code: string | null;
	description: string | null;
	currency: string;
	is_pension: boolean;
	allowed_custodies: ('onshore' | 'offshore')[];
	allowed_client_types: ('individual' | 'organisation')[];
	allowed_currencies: string[];
	max_parties: number;
	min_age: number | null;
	max_age: number | null;
	bank_details_allowed: boolean;
	enabled: boolean;
	sort_order: number;
	platforms: PlatformRef[];
};

export type Allocation = {
	id?: number;
	instrument_id: number | null;
	external_asset_id: string | null;
	isin: string | null;
	name: string | null;
	percentage: number | string;
};

export type ModelVersion = {
	id: number;
	/** The row's address — `/catalogue/…/{uid}` takes it; the id is what the
	 *  payload's own joins key on. */
	uid: string;
	version: number;
	effective_from: string | null;
	cash_allocation: number | string | null;
	note: string | null;
	published_at: string | null;
	created_at: string;
	allocations: Allocation[];
};

export type Commentary = {
	id: number;
	/** The row's address — `/catalogue/…/{uid}` takes it; the id is what the
	 *  payload's own joins key on. */
	uid: string;
	title: string | null;
	body: string;
	period_start: string | null;
	period_end: string | null;
	model_version_id: number | null;
	published_at: string | null;
	created_at: string;
	updated_at: string;
};

export type Model = {
	id: number;
	/** The row's address — `/catalogue/…/{uid}` takes it; the id is what the
	 *  payload's own joins key on. */
	uid: string;
	name: string;
	code: string | null;
	model_type: string | null;
	aim: string | null;
	target_return: number | string | null;
	target_return_basis: string | null;
	target_return_horizon_years: number | null;
	benchmark: string | null;
	risk_rating: string | null;
	risk_profile: string | null;
	investment_decision: string | null;
	model_charge: number | string | null;
	vatable: boolean | null;
	suspend_rebalances: boolean;
	cash_allocation: number | string | null;
	currency: string;
	status: string;
	enabled: boolean;
	/** The wrappers the model may be held in (`platform.models.products`), populated by the
	 *  materialiser from what the platform publishes. What the catalogue page filters by. */
	products: string[] | null;
	live_version: number | null;
	live_effective_from: string | null;
	version_count: number;
	commentary_count: number;
};

/** A model plus its whole history — `GET …/catalogue/models/{uid}`. */
export type ModelDetail = Model & {
	platforms: { provider: string; external_id: string | null }[];
	versions: ModelVersion[];
	commentary: Commentary[];
};

export type FeeTier = { from: number; to: number | null; rate: number };
export type FeeRule = { amount?: number; rate?: number; tiers?: FeeTier[] };

export type FeePackage = {
	id: number;
	/** The row's address — `/catalogue/…/{uid}` takes it; the id is what the
	 *  payload's own joins key on. */
	uid: string;
	name: string;
	category: string;
	description: string | null;
	/** The provider's own code for a hand-authored package — for SEI, the one-time `FEE_TYPE` code
	 *  an ad-hoc charge is sent under. Null for a package with no provider code. */
	external_id: string | null;
	frequency: 'initial' | 'ongoing';
	fee_type: 'fixed' | 'percentage' | 'tiered';
	rule: FeeRule;
	access: string;
	charge_basis: string | null;
	payer: string | null;
	vatable: boolean | null;
	minimum_amount: number | string | null;
	maximum_amount: number | string | null;
	applies_to: string[] | null;
	effective_from: string | null;
	effective_to: string | null;
	currency: string;
	enabled: boolean;
	is_default: boolean;
	optional: boolean;
	sort_order: number;
	product_id: number | null;
	model_id: number | null;
	product_name: string | null;
	model_name: string | null;
};

export type Provider = {
	id: number;
	/** The row's address — `/catalogue/…/{uid}` takes it; the id is what the
	 *  payload's own joins key on. */
	uid: string;
	name: string;
	description: string | null;
	enabled: boolean;
	masterparty_id?: string | null;
	origo_id?: string | null;
	atg_reference?: string | null;
	/** The platform's own status. Absent for a hand-added provider. */
	status?: string | null;
	/** Rails the provider is reachable on, each carrying its own products (0046). */
	rails?: { rail: string; reference: string | null; products: string[] }[];
	/** Union of the products across rails — what the register is filtered by. */
	product_types?: string[];
	/** The single wrapper a third-party provider provides (0130), from the register's own
	 *  `data.wrapper_type` and aliased onto the shelf's vocabulary. Null when the register did not
	 *  say. Transfer providers carry `product_types` instead — a ceding scheme takes several. */
	product?: string | null;
};

export type Instrument = {
	id: number;
	isin: string | null;
	sedol: string | null;
	name: string;
	asset_type: string | null;
	currency: string | null;
	exchange_code: string | null;
};

export type Term = {
	key: string;
	category: string;
	default_label: string;
	default_plural: string | null;
	description: string | null;
	label: string | null;
	plural: string | null;
	effective_label: string;
	effective_plural: string | null;
	overridden: boolean;
	updated_at: string | null;
};

export type Catalogue = {
	/** The provider registry. Served from CODE since 0131 (`app/providers.py::PROVIDERS`), so `id`
	 *  IS the provider code — the two are returned as the same value because the surrogate the
	 *  callers keyed on was a stand-in for exactly it. */
	platforms: { id: string; name: string; provider: string }[];
	instruments: Instrument[];
	products: Product[];
	models: Model[];
	fee_packages: FeePackage[];
	third_party_providers: Provider[];
	transfer_providers: Provider[];
	terms: Term[];
};

/** What each wrapper type is CALLED. The enum member is a code, not a name — "TPONBOND" is what
 *  an API call carries, "Third-party Onshore Bond" is what a person reads.
 *
 *  Kept in lockstep with `app/materialise.py::PRODUCT_LABELS`, which writes the same string as a
 *  discovered product's `name` so every other surface reads properly too. This map is still needed
 *  here because the products table renders a row for every type in the vocabulary, including ones
 *  the firm has no record for yet — those have no `name` to read. */
export const PRODUCT_TYPE_LABELS: Record<string, string> = {
	GIA: 'General Investment Account',
	ISA: 'Individual Savings Account',
	JISA: 'Junior Individual Savings Account',
	SIPP: 'Self-Invested Personal Pension',
	TPONBOND: 'Third-party Onshore Bond',
	TPOFFBOND: 'Third-party Offshore Bond',
	TPPENSION: 'Third-party Pension'
};

/** The wrapper types `platform.product_type` allows. */
export const PRODUCT_TYPES = [
	'GIA',
	'ISA',
	'JISA',
	'SIPP',
	'TPONBOND',
	'TPOFFBOND',
	'TPPENSION'
] as const;

/** Advised vs discretionary — a Graphene-side classification, rendered as a button group
 * because it is a two-way choice that changes what the model means.
 *
 * Deliberately NOT Seccl's `investmentDecision`: that field holds the decision MAKER's
 * identity ({firmLei, personId, personIdType}), verified against staging. On the Seccl side the
 * advised/discretionary distinction lives on an account's wrapperDetail, so this value is ours
 * and the sync leaves it alone. */
export const INVESTMENT_DECISIONS = ['Advised', 'Discretionary'] as const;

/** Seccl's `modelType`. Reference only: the models form shows the value read-only, because the
 *  sync assigns it unconditionally and an edit would last until the next run. */
export const MODEL_TYPES = ['Model', 'Blend', 'Bespoke'] as const;

/** Seccl's `assetMix.instrumentType` — the equity/instrument classes a model allocates across.
 *
 * NOT A MODEL FIELD, and this is the note that says so. `assetMix` describes an allocation LINE:
 * `platform.models` has no such column, `catalogue.py::_MODEL_FIELDS` does not list one, and the
 * form action never posted it. The models form used to render pickers for this and RATE_TYPES
 * anyway — controls an operator could set and nothing anywhere would keep. Kept as the vocabulary
 * for a future allocation-line editor, which is where it would actually belong. */
export const INSTRUMENT_TYPES = [
	'Equity',
	'Fund',
	'Investment Trust',
	'ETF',
	'Bond',
	'Gilt',
	'Cash',
	'Structured Product'
] as const;

/** Seccl's `assetMix.instrumentRateType`. Also an allocation-line property — see INSTRUMENT_TYPES. */
export const RATE_TYPES = ['Fixed', 'Variable', 'Index Linked'] as const;

export const RISK_RATINGS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'] as const;

export const FEE_TYPES = ['fixed', 'percentage', 'tiered'] as const;
export const FREQUENCIES = ['initial', 'ongoing'] as const;
export const CHARGE_BASES = ['aum', 'contribution', 'flat', 'transaction'] as const;
export const PAYERS = ['client', 'firm', 'provider'] as const;
export const ACCESS = ['hidden', 'display', 'select', 'custom'] as const;

/** How a fee reads in a table — the rule shapes are three different sentences. */
export function describeFee(fee: FeePackage): string {
	return describeFeeRule(fee) || '—';
}

/** Allocations plus cash, so the editor can show the total before the API rejects it. */
export function allocationTotal(lines: Allocation[], cash: number | string | null): number {
	const sum = lines.reduce((total, line) => total + Number(line.percentage || 0), 0);
	return Math.round((sum + Number(cash || 0)) * 1e6) / 1e6;
}
