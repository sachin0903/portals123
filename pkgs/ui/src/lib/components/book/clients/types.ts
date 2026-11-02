/**
 * The data shapes the shared clients surfaces render, defined STRUCTURALLY here rather than
 * imported from an app: pkgs/ui cannot depend on `apps/platform`'s `$lib/api`, and the admin
 * portal will feed the same components from its own loaders (`?tenant=` calls against the same
 * endpoints). An app's richer API types (e.g. platform's `Client`, `ContactDetail`) are assignable
 * to these because they are supersets — TypeScript checks the fields the components actually read.
 */
import { TITLE_OPTIONS, withStoredChoice } from '../../../forms/vocabularies.js';
import type { StandingMovement } from './standing.js';

export type ClientType = 'individual' | 'organisation';
export type ServiceType = 'advised' | 'execution_only';

/**
 * Courtesy titles offered on the client record, onboarding and beneficiary nominations.
 *
 * The `title` field type's list (`forms/vocabularies.ts`): the titles every custodian can
 * store. Seccl's enum word for Prof is Professor, and enrolment sends that. Mx is in
 * Seccl's enum and is not offered. Master, Capt, Reverend, Sir, Lord and Lady are Morgan
 * Lloyd's and are not offered. A title already on the record outside this list still
 * displays. The mapping is in `docs/client-vocabularies.md`.
 */
export const PERSON_TITLES = TITLE_OPTIONS.map((option) => option.value);

/** The same closed title list the onboarding applicant form offers. */
export const PERSON_TITLE_ITEMS = TITLE_OPTIONS.map((option) => ({
	value: option.value,
	label: option.label
}));

/** The shared list, plus a title already on the record that the list does not name.
 *
 * A synced Baroness has to stay visible in the editor. A bare number is the custodian's
 * code, not a title, so it is not offered as a choice. */
export function titleItems(current?: string | null): { value: string; label: string }[] {
	return withStoredChoice(TITLE_OPTIONS, current, { digitsAreCodes: true });
}

/** A client's postal address (crm.contacts.address jsonb). All parts optional. */
export type ClientAddress = {
	line1?: string | null;
	line2?: string | null;
	city?: string | null;
	postcode?: string | null;
	country?: string | null;
};

/** The FCA's four drivers of vulnerability (FG21/1 "Guidance for firms on the fair treatment of
 *  vulnerable customers"). Their vocabulary, not ours, so a driver is spelled the same word in
 *  this record, on the badge, in a file review and in the API's own validation
 *  (`app/contacts.py::VULNERABILITY_REASONS` — the two must not drift). */
export type VulnerabilityReason = 'health' | 'life_event' | 'resilience' | 'capability';

/** The FCA's labels, in the FCA's listed order — which is also the order the API returns the
 *  reasons in, so the badge row does not reshuffle between saves. */
export const VULNERABILITY_LABELS: Record<VulnerabilityReason, string> = {
	health: 'Health',
	life_event: 'Life event',
	resilience: 'Resilience',
	capability: 'Capability'
};

/** The 24 CHARACTERISTICS beneath those four drivers (FG21/1, 0116) — and exactly the set Morgan
 *  Lloyd's `cam_vulnerabilitycharacteristics` accepts, so the regulator's vocabulary is the
 *  vocabulary at both ends. Pinned against `app/contacts.py::VULNERABILITY_CHARACTERISTICS` and
 *  the 0116 CHECK by `apps/api/tests/test_contacts.py`, for the reason the drivers are: the API
 *  container ships without pkgs/ui, so nothing but a test holds the two together, and a
 *  mis-spelled slug renders as the raw value beside correctly-labelled neighbours. */
export type VulnerabilityCharacteristic =
	| 'physical_disability'
	| 'long_term_illness'
	| 'hearing_impairment'
	| 'visual_impairment'
	| 'mental_health'
	| 'addiction'
	| 'low_mental_capacity'
	| 'retirement'
	| 'bereavement'
	| 'income_shock'
	| 'relationship_breakdown'
	| 'domestic_abuse'
	| 'caring_responsibilities'
	| 'other_circumstances'
	| 'erratic_income'
	| 'over_indebtedness'
	| 'low_savings'
	| 'low_emotional_resilience'
	| 'low_financial_knowledge'
	| 'poor_literacy_numeracy'
	| 'poor_english'
	| 'poor_digital_skills'
	| 'learning_difficulties'
	| 'no_access_to_support';

export const VULNERABILITY_CHARACTERISTIC_LABELS: Record<VulnerabilityCharacteristic, string> = {
	physical_disability: 'Physical disability',
	long_term_illness: 'Severe or long-term illness',
	hearing_impairment: 'Hearing impairment',
	visual_impairment: 'Visual impairment',
	mental_health: 'Mental health condition or disability',
	addiction: 'Addiction',
	low_mental_capacity: 'Low mental capacity or cognitive disability',
	retirement: 'Retirement',
	bereavement: 'Bereavement',
	income_shock: 'Income shock',
	relationship_breakdown: 'Relationship breakdown',
	domestic_abuse: 'Domestic abuse (including economic control)',
	caring_responsibilities: 'Caring responsibilities',
	other_circumstances: 'Other circumstances affecting their experience of financial services',
	erratic_income: 'Inadequate (outgoings exceed income) or erratic income',
	over_indebtedness: 'Over-indebtedness',
	low_savings: 'Low savings',
	low_emotional_resilience: 'Low emotional resilience',
	low_financial_knowledge: 'Low knowledge or confidence in managing finances',
	poor_literacy_numeracy: 'Poor literacy or numeracy skills',
	poor_english: 'Poor English language skills',
	poor_digital_skills: 'Poor or non-existent digital skills',
	learning_difficulties: 'Learning difficulties',
	no_access_to_support: 'No or low access to help or support'
};

/** Which driver each characteristic sits under, so the MultiSelect can group 24 options under
 *  the four headings an adviser already thinks in.
 *
 *  PRESENTATIONAL ONLY. It is FG21/1's own table and the vendor lists them in the same order,
 *  which is good evidence — but nothing enforces it and no characteristic is gated on its
 *  driver being ticked (0116 says why): if the grouping were slightly wrong the cost would be an
 *  option under the wrong heading, not an answer nobody can record. */
export const CHARACTERISTICS_BY_DRIVER: Record<VulnerabilityReason, VulnerabilityCharacteristic[]> =
	{
		health: [
			'physical_disability',
			'long_term_illness',
			'hearing_impairment',
			'visual_impairment',
			'mental_health',
			'addiction',
			'low_mental_capacity'
		],
		life_event: [
			'retirement',
			'bereavement',
			'income_shock',
			'relationship_breakdown',
			'domestic_abuse',
			'caring_responsibilities',
			'other_circumstances'
		],
		resilience: ['erratic_income', 'over_indebtedness', 'low_savings', 'low_emotional_resilience'],
		capability: [
			'low_financial_knowledge',
			'poor_literacy_numeracy',
			'poor_english',
			'poor_digital_skills',
			'learning_difficulties',
			'no_access_to_support'
		]
	};

/** The 24 characteristics as MultiSelect options, grouped under FG21/1's driver labels and in
 *  that table's order — so the picker and the stored array sort the same way. */
export const CHARACTERISTIC_SELECT_OPTIONS: {
	value: VulnerabilityCharacteristic;
	label: string;
	group: string;
}[] = (
	Object.entries(CHARACTERISTICS_BY_DRIVER) as [
		VulnerabilityReason,
		VulnerabilityCharacteristic[]
	][]
).flatMap(([driver, slugs]) =>
	slugs.map((value) => ({
		value,
		label: VULNERABILITY_CHARACTERISTIC_LABELS[value],
		group: VULNERABILITY_LABELS[driver]
	}))
);

/** The member's own tax-relief declaration (Morgan Lloyd `cam_taxrelief`, 0116). Never derived
 *  from holding an earnings figure — the four options are first-person statements. */
export type TaxRelief =
	| 'uk_relevant_earnings'
	| 'uk_resident_this_year'
	| 'overseas_crown_employment_no_uk_earnings'
	| 'spouse_overseas_crown_employment';

/** Shortened from the vendor's full first-person wording, which runs to three lines. */
export const TAX_RELIEF_LABELS: Record<TaxRelief, string> = {
	uk_relevant_earnings: 'Has relevant UK earnings chargeable to income tax this tax year',
	uk_resident_this_year: 'Is or will be UK resident at some time during this year',
	overseas_crown_employment_no_uk_earnings:
		'In overseas Crown employment, with no UK-taxable earnings this tax year',
	spouse_overseas_crown_employment:
		'Spouse has UK-taxable earnings from overseas Crown employment this tax year'
};

/** The contact identity every clients surface reads (a `crm.contacts` row's display fields). */
export type ClientContact = {
	id: number;
	uid: string;
	type: ClientType;
	/** An organisation's kind (`trust` | `company` | `charity`), and null for an individual —
	 *  0002's CHECK allows it only on an organisation. Optional because the LISTING projects the
	 *  collapsed `client_type` instead; the RECORD carries this. */
	sub_type?: string | null;
	service_type: ServiceType | null;
	enabled: boolean;
	/** The firm's Close. Live lists filter it out; the record itself stays loadable so the
	 *  header can say Closed. */
	deleted_at?: string | null;
	/** The custodian's own status for the person (Seccl `suspended`, Morgan Lloyd `inactive`).
	 *  Never written by the product — the listing derives Suspended from it. */
	provider_status?: string | null;
	/** The client's base currency (ISO 4217). Seccl and Morgan Lloyd are GBP; SEI publishes
	 *  `CLIENT_BASE_CURRENCY`. Absent on a caller that has not fetched it — display falls back to GBP. */
	currency?: string | null;
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
	email: string | null;
	/** The mobile. Required, not optional: both the listing and the record project it. */
	mobile: string | null;
	/** The other number. Optional because a caller may not have fetched it; null and absent
	 *  both mean none. */
	phone?: string | null;
	address?: ClientAddress | null;
	/** One set of bank details for the person. Accounts with none of their own use these. */
	bank_account?: unknown;
	/** Vulnerability (0054). Optional because the clients LISTING endpoint omits the block — the
	 *  record's own load (`GET /crm/contacts/{id}`) is what carries it. Absent is not the same as
	 *  "not vulnerable": a surface that has not fetched it must not draw the flag either way. */
	is_vulnerable?: boolean | null;
	vulnerability_reasons?: VulnerabilityReason[] | null;
	/** The characteristics beneath those drivers (0116). Optional where a driver is required: a
	 *  flag raised in a first conversation often has the driver before it has the specifics. */
	vulnerability_characteristics?: VulnerabilityCharacteristic[] | null;
	/** Whether it is expected to pass. `null` = not established, a real third state. */
	vulnerability_is_temporary?: boolean | null;
	/** When the flag was last set, so a temporary vulnerability has a review point. */
	vulnerability_recorded_at?: string | null;
	vulnerability_notes?: string | null;
	/** The fact-find's own answers (0052-0053), written by the onboarding mapping. Optional for the
	 *  same reason as the vulnerability block: the LISTING endpoint omits them, and absent is not
	 *  the same as empty — a surface that has not fetched them must not render them as "—". */
	middle_name?: string | null;
	dob?: string | null;
	gender?: string | null;
	nationality?: string[] | string | null;
	tax_residency?: string[] | string | null;
	national_identifier?: string | null;
	national_identifier_type?: string | null;
	marital_status?: string | null;
	employment_status?: string | null;
	retirement_age?: number | null;
	source_of_wealth?: string | null;
	/** 0116 — the KYC answers onboarding always collected and had nowhere to put, so they folded
	 *  into `data` and reached this record only as a dotted path under the generic overflow.
	 *  `occupation` is NOT `employment_status`: what they do vs whether they work. */
	occupation?: string | null;
	occupation_detail?: string | null;
	/** ISO code, the same shape `nationality` stores. `*_name` is resolved from `ref.countries`
	 *  by the record's own read, so a surface shows the country and posts the code — never "GB"
	 *  as a fact about where someone was born. */
	country_of_birth?: string | null;
	country_of_birth_name?: string | null;
	nationality_names?: string[] | null;
	tax_residency_names?: string[] | null;
	town_of_birth?: string | null;
	source_of_funds?: string | null;
	source_of_funds_detail?: string | null;
	tax_relief?: TaxRelief | string | null;
	/** The MPAA trigger — it cuts the annual allowance from £60k to £10k, so it is a fact worth
	 *  stating on the record rather than one buried in a pension calculation. */
	flexi_access_taken?: boolean | null;
	/** EMPLOYMENT (0126 + 0051), shown only for a client who holds a pension.
	 *
	 *  `employer_name` is who they work for — moved here from `platform.contributions`' per-payment
	 *  contributor block, because who somebody works for is a fact about them rather than about one
	 *  £500 credit, and the classify panel was asking for it once per payment.
	 *
	 *  `relevant_uk_earnings` has had a column since 0051 and had no editor and no read anywhere:
	 *  it is the HMRC figure a relievable contribution is tested against, so without it the
	 *  eligibility check silently fell back to the £3,600 floor for every client. Shown as
	 *  **"Annual earnings"** — the phrase an adviser, a client and Morgan Lloyd's own
	 *  `cam_annualearnings` all use — while the COLUMN keeps the statutory name, because that is
	 *  what the relief maths computes with and not everything on a P60 qualifies. */
	employer_name?: string | null;
	relevant_uk_earnings?: number | null;
	/** 0127 — the fields the restructured record's sections ask for.
	 *
	 *  `employer_contributions` gates the Employer section and is ASKED rather than derived from the
	 *  ledger: a member whose employer is about to start has no rows yet and still needs the company
	 *  captured before the first payment.
	 *
	 *  `flexi_access_date` is a different fact from `flexi_access_taken` — it decides WHICH tax year
	 *  the £10,000 MPAA first applies to, which the flag alone cannot answer. */
	employer_contributions?: boolean | null;
	employer_registration_number?: string | null;
	employer_address?: Record<string, unknown> | null;
	/** The employer's own bank account (0271) — the scheme paying employer contributions in. The
	 *  same shape as `bank_account`, read with `readBankDetails` and shown on the Employer card. */
	employer_bank_account?: Record<string, unknown> | null;
	flexi_access_date?: string | null;
	source_of_wealth_detail?: string | null;
	/** The W-8/W-9 self-certification (0121), required by SEI enrolment and by nobody else. The
	 *  client's own DECLARATION and never inferred from `tax_residency`: a signed certification to a
	 *  tax authority is the `tax_relief` rule exactly. */
	us_person?: boolean | null;
	fatca_status?: string | null;
	/** The signed W-9 or W-8, filed in the client's documents. Linked only while `fatca_status` is
	 *  `w9` or `w8`; the API unlinks it when the answer changes, and the file stays filed. */
	fatca_document?: { uid: string; name: string } | null;
	/** The passport SEI enrolment asks for. `passport_country` is an ISO code, like `nationality`
	 *  and `tax_residency` — resolved to a name for display by `countryName`. */
	passport_number?: string | null;
	passport_country?: string | null;
	fatca_certified_at?: string | null;
	/** Everything the fact-find gathered that has no column: offshore KYC, employer contributions.
	 *  Nested by `step_handlers._write_contact_patch`. Shown as gathered rather than interpreted —
	 *  the record cannot know what a firm's own added questions mean. */
	data?: Record<string, unknown> | null;
};

/** One row of the clients LISTING (GET /crm/contacts, plus the lake-derived money columns). */
export type ClientListRow = ClientContact & {
	account_count: number;
	/** JISA accounts this contact represents but does not own; status only, never their AUM. */
	represented_account_count?: number;
	/**
	 * The distinct wrappers this client holds (`GIA`, `ISA`, `SIPP`, `JISA`…), sorted by the API.
	 *
	 * What the listing shows in place of the bare count: "which of my clients has a SIPP" is a
	 * question a book is actually scanned for, and a number cannot answer it. Every account they
	 * hold, the same population the count covered — a closed GIA is still a GIA held with us.
	 */
	account_types: string[];
	/**
	 * The client's kind, at its most specific: `individual`, or the organisation's `sub_type`
	 * (`trust`/`company`/`charity`) falling back to `organisation`. Decided by the API — see
	 * `client-type.ts` for why it is not derived twice.
	 */
	client_type: string | null;
	advisers: string[];
	/** Provider codes of the custodians holding this client's accounts. A client matches a
	 *  custodian filter when any of their accounts is held there. */
	custodians?: string[];
	portfolio_value: number;
	/** Null when no valued account has a stated figure. Zero is a measured flat position. */
	cash: number | null;
	growth: number | null;
};

/** Bank details for a client (crm.contacts.bank_account jsonb) — one set per person.
 *  UK (BACS) rows carry sort_code + account_number; international rows carry iban + bic.
 *
 *  `method` is the UK rail only — BACS (free, the default) or Faster Payment, which some firms
 *  pay by instead. Absent means BACS: the default is not written, the same convention `bank`
 *  already follows. An international row has no method to choose — it always reaches SEI over
 *  SWIFT, which is what having an IBAN already says. */
export type BankDetails = {
	name?: string | null;
	sort_code?: string | null;
	account_number?: string | null;
	bank?: string | null;
	iban?: string | null;
	bic?: string | null;
	/** Account-level provider rows may carry rails the client editor does not offer. */
	method?: string | null;
};

/** An account owned by the client, as the record's accounts table renders it. */
export type ClientAccountRow = {
	id: number;
	/** NOT NULL since `0111_accounts_uid_not_null` — it is how the row links to the account. */
	uid: string;
	name: string | null;
	status: string;
	value: number | null;
	/** The account's base currency. Absent on a lean payload — display falls back to the client's. */
	currency?: string | null;
	product_name: string | null;
	product_type: string | null;
	/** The service the firm provides on the account — labelled by `serviceTypeLabel`, decided by
	 *  the API (the same expression the accounts listing reads), null when nothing we hold says. */
	service_type: string | null;
	/** Whether the wrapper is a pension, from `platform.products.is_pension` rather than from a
	 *  word. This is what shows the Pension and Employer sections. An account whose product
	 *  parent has not resolved reads false, which is the same answer the wrapper set gave it. */
	is_pension: boolean;
	model_id: number | null;
	model_name: string | null;
	cash: number;
	growth: number;
	/** Whether accounts of this product may hold withdrawal destination bank details.
	 *  Absent means allowed — the API defaults the column to true. */
	bank_details_allowed?: boolean;
	/** This account's own pay-to details. Empty or absent means it uses the client's. */
	bank_accounts?: unknown;
	/** SEI's rail tariff, or null when this custodian does not charge one. */
	withdrawal_fees?: { faster_payment: number; swift: number } | null;
	/**
	 * The account's STANDING arrangements (`platform.accounts.{payments,withdrawals}`), which the
	 * record flattens into one panel across every account — a regular payment is something the CLIENT
	 * set up, and they think of it as "my £100 a month" rather than as a property of one wrapper.
	 *
	 * Optional because this component is also mounted over the LISTING shape, which does not carry
	 * them: absent means "not fetched", and the panel must not read that as "none set up".
	 */
	payments?: StandingMovement[] | null;
	withdrawals?: StandingMovement[] | null;
};

/** A money-movement instruction — GET /firm/contacts/{id}/instructions. */
export type InstructionKind =
	'expected_in' | 'withdrawal' | 'adhoc_fee' | 'model_topup' | 'model_withdrawal';
export type InstructionRow = {
	uid: string;
	kind: InstructionKind;
	amount: number;
	/** Provider payment-rail charge. Reserved from the client only when `pass_rail_fee` is not false. */
	rail_fee?: number | null;
	/** Adviser charge taken from a payment in. Zero / absent on every other kind. */
	adviser_fee?: number | null;
	/** The rail this withdrawal pays over. Absent on older rows and on every other kind. */
	payment_method?: string | null;
	/** When false the firm pays `rail_fee`. Absent means it is passed on, which older rows were. */
	pass_rail_fee?: boolean;
	status: 'pending' | 'settled' | 'cancelled';
	due_date: string | null;
	note: string | null;
	created_at: string;
	/** Which account the instruction is a claim against — a *pending* row still promises that
	 *  account's money away, so the screen's "available" figure has to net them off. */
	account_id: number;
	/** The account's uid — what an href is built from. The serial above is what the API's
	 *  own filters and joins key on; a URL never carries it (docs/api.md → Addressing). */
	account_uid: string;
	account_name: string | null;
	model_name: string | null;
	/** Which of the firm's fee packages an ad hoc fee is charged under, and its name. Optional
	 *  because a listing-shaped read predates the column, and null on every other kind. */
	fee_package_id?: number | null;
	fee_package_name?: string | null;
};

export type ClientNoteRow = {
	id: number;
	/** The public handle. Optional because a listing-shaped read predates it; the delete affordance
	 *  renders only when one is present, since a URL never carries the serial beside it. */
	uid?: string;
	body: string;
	author: string | null;
	created_at: string;
};

/** One person on the client's team (`crm.client_advisers`). The display fields the header states,
 *  plus the id the Team popout needs to remove one — optional because the LISTING shape carries
 *  names alone. `role` is one of `CLIENT_TEAM_ROLES` (`team.ts`): at most one `adviser`, any number
 *  of the rest. The Adviser tag follows `role`, not `is_adviser` — the staff flag says who MAY be
 *  a client's adviser, not who is this one's. */
export type ClientAdviserRow = {
	name: string | null;
	email: string | null;
	adviser_id?: string;
	role?: string | null;
	job_title?: string | null;
	is_adviser?: boolean;
};

/** A client-access profile linked to the client, and the capacity it acts in (crm.contact_users). */
export type ClientUserRow = {
	user_id: string;
	capacity: string;
	name: string | null;
	email: string | null;
};

/** One of the firm's identities, as the Advisers / Profiles popouts' pickers offer them —
 *  `GET /firm/members`. `portals` is what says whether they can be an adviser (platform) or a
 *  client profile (client), so the two pickers offer different halves of one list. */
export type FirmMemberOption = {
	user_id: string;
	email: string | null;
	name?: string | null;
	/** Whether this person may be a client's adviser (`auth.users.is_adviser`). The team picker
	 *  offers only these for the Adviser role, because the API refuses anyone else. */
	is_adviser?: boolean;
	/** `auth.users.type` — `staff`, `client`, both or neither; the API sends the same array as
	 *  `types` and `type`. The team's other roles need staff (the API refuses a client login), so
	 *  the picker offers only those. */
	types?: string[] | null;
	type?: string[] | null;
	/** `status` gates the two pickers too: a deactivated adviser/client keeps their row (portal
	 *  access is never deleted, only marked `inactive`), so matching on `portal` alone offered
	 *  someone long after they lost access to it. */
	portals: { portal: string; status: string }[];
};

/** The capacities a profile may act in (crm.contact_users.capacity) — the API's own vocabulary.
 *  One definition for the picker and the label beside a linked profile. */
export const CAPACITY_LABELS: Record<string, string> = {
	self: 'Self',
	authorised: 'Authorised',
	power_of_attorney: 'Power of attorney',
	delegate: 'Delegate'
};

export const CAPACITY_ITEMS = Object.entries(CAPACITY_LABELS).map(([value, label]) => ({
	value,
	label
}));

/** Which band of the client record to render. The platform routes Overview and Details now and
 *  the sidebar is the strip, so it mounts this once per section with the header riding along.
 *  Defaults to `all` — a mount with no routes for the bands (the admin portal's read-only one)
 *  gets the whole record stacked rather than a third of it. */
export type ClientRecordView = 'overview' | 'details' | 'all';

/** The slice of GET /crm/contacts/{id} the record component renders. The platform's fuller
 *  `ContactDetail` (users, kyc, relationships, …) is assignable to it. */
export type ClientRecordDetail = {
	contact: ClientContact;
	advisers: ClientAdviserRow[];
	accounts: ClientAccountRow[];
	notes: ClientNoteRow[];
	/** The client's portal logins. OPTIONAL, and absent is not empty: the badge beside the name
	 *  (which opens the Logins popout) renders only when a loader has supplied them, because
	 *  "No portal access" against a read that never asked misreports the client rather than the
	 *  load. */
	users?: ClientUserRow[];
	/** `crm.kyc_checks` — identity, AML and terms. OPTIONAL, and absent is not empty: the
	 *  Compliance card says "not recorded" for a check it has not been given, which is honest,
	 *  where an empty array from a read that never asked would claim the check was never done. */
	kyc?: ComplianceCheck[];
	/** Accepted legal terms (version-pinned). OPTIONAL — the Compliance card lists them when given. */
	term_acceptances?: TermAcceptance[];
	/** Directed CRM relationships returned from either end of the edge. */
	relationships?: ClientRelationship[];
};

export type ClientRelationship = {
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

/** One of the firm's investment models — GET /crm/models. The mandate picker needs the name;
 *  the rest is what the picker states about the model being chosen, so the adviser reads what
 *  they are moving the account to rather than a bare name. Percentage points (6.5 = 6.5%). */
export type CatalogModel = {
	id: number;
	name: string;
	code?: string | null;
	model_type?: string | null;
	risk_rating?: string | number | null;
	target_return?: number | string | null;
	model_charge?: number | string | null;
};

/** An open action item on the client, PRESENTATION-READY: the app computes due phrasing and
 *  overdue-ness with its own task rules (apps/platform `$lib/tasks`) so the two portals cannot
 *  disagree with the surfaces that own tasks. */
export type ClientTaskItem = {
	uid: string;
	title: string;
	overdue: boolean;
	/** "Due today" / "3 days overdue" / null when the task carries no due date. */
	dueLabel: string | null;
};

/** One row of `ref.countries` (0050), as the record's pickers need it. Structurally a subset of
 *  what `GET /ref/countries` returns, so the full row is assignable. */
export type CountryOption = {
	code: string;
	name: string;
	/** "British". What a NATIONALITY picker labels with — see `national-identifier.ts`. */
	adjective?: string | null;
	/** Drives the identifier preference order (the UK counts as EEA whatever this says). */
	in_eea?: boolean | null;
	/** `[{name, regex, example, priority}]` — which identifier this country asks for, in order. */
	identifiers?: unknown;
};

/** Morgan Lloyd's `cam_sourceofwealth` option set, verbatim including their own misspelling of
 *  "Inheritence".
 *
 *  THE VENDOR'S LIST, not ours, and it is offered as a PICKER rather than a text box for a reason
 *  the integration already records: `_SOURCE_OF_WEALTH` sends their INT only on an exact label
 *  match, so a firm typing "Investments" or "salary " filled our column and left the vendor's
 *  structured field empty. Picking from the list is what makes the mapping hit.
 *
 *  Kept as labels rather than codes because `crm.contacts.source_of_wealth` is TEXT against a list
 *  that is theirs and grows (0053's reasoning); the int is resolved at the boundary. */
export const SOURCE_OF_WEALTH_OPTIONS = [
	'Salary',
	'Variable Remuneration',
	'Investment Income or Sale of Investments',
	'Pension Income',
	'Sale of Main Residence',
	'Sale of Other Property',
	'Business Interests',
	'Inheritence',
	'Gift',
	'Financial Settlements',
	'Lottery/Betting/Casino',
	'Other'
] as const;

/** Where this client's money came from. Client-level: onboarding asks it once per person
 *  (`crm.contacts.source_of_funds`), distinct from source of wealth, which is how the money
 *  was accumulated. */
export const SOURCE_OF_FUNDS_OPTIONS = [
	'Personal savings',
	'Salary or employment income',
	'Business income',
	'Sale of property',
	'Sale of investments (e.g., shares, bonds)',
	'Loan proceeds',
	'Gift from family or friends',
	'Inheritance',
	'Pension or retirement funds',
	'Insurance payout',
	'Transfer from another bank account',
	'Transfer from another custodian or investment platform',
	'Payment from a current account',
	'Proceeds from company sale',
	'Other (please specify)'
] as const;

/** The occupation list, as the firm supplied it.
 *
 *  NOTE ON PROVENANCE: it arrived labelled "source of funds" and it is not one — every member is a
 *  JOB (Accountant, Airline Pilot, Retired, Homemaker), where a source of funds is where a
 *  particular payment came from (salary, savings, the sale of a property). Filing it under source
 *  of funds would put "Barrister" in a field an AML reviewer reads as the origin of the money, so
 *  it is the OCCUPATION vocabulary — which is the field the record already had, as free text.
 *
 *  A PICKER rather than a box for the reason source of wealth is one: `occupation` is sent to
 *  Morgan Lloyd and read by screening, and ninety-seven agreed answers are worth more than an
 *  adviser's own spelling of the same job. `occupation_detail` is only asked for **Other**
 *  (please specify) and **Retired** (previous occupation). `allowCustom` on the combobox means a
 *  job nobody listed can still be typed — a closed list of ninety-seven would otherwise make some
 *  client unrecordable.
 */
export const OCCUPATION_OPTIONS = [
	'Accountant',
	'Actuary',
	'Airline Pilot',
	'Architect',
	'Auditor',
	'Bar Staff',
	'Barrister',
	'Border Force Officer',
	'Broadcaster',
	'Builder',
	'Business Analyst',
	'Carpenter',
	'Chef',
	'Civil Engineer',
	'Civil Servant',
	'Compliance Consultant',
	'Compliance Officer',
	'Copywriter',
	'Corporate Lawyer',
	'Cybersecurity Analyst',
	'Data Scientist',
	'Delivery Driver',
	'Dentist',
	'DevOps Engineer',
	'Diplomat',
	'Director',
	'Editor',
	'Education Consultant',
	'Electrician',
	'Engineer',
	'Entrepreneur',
	'Event Planner',
	'Financial Advisor',
	'Firefighter',
	'Fleet Manager',
	'Fund Manager',
	'GP / Doctor',
	'Graphic Designer',
	'Headteacher',
	'Health & Safety Officer',
	'Healthcare Assistant',
	'Homemaker',
	'Hotel Manager',
	'Insurance Underwriter',
	'Investment Analyst',
	'IT Consultant',
	'Journalist',
	'Legal Counsel',
	'Local Government Officer',
	'Logistics Manager',
	'Management Consultant',
	'Marketing Manager',
	'Medical Researcher',
	'Military Personnel',
	'Network Administrator',
	'Notary',
	'Nurse',
	'Paralegal',
	'Paramedic',
	'Patent Attorney',
	'Pharmacist',
	'Plumber',
	'Police Officer',
	'Primary School Teacher',
	'Product Manager',
	'Psychologist',
	'Public Relations Officer',
	'QA Engineer',
	'Quantity Surveyor',
	'Radiographer',
	'Relationship Manager',
	'Researcher',
	'Retail Manager',
	'Retired',
	'Risk Manager',
	'School Administrator',
	'Secondary School Teacher',
	'Self-Employed',
	'Shipping Coordinator',
	'Site Manager',
	'Social Media Manager',
	'Social Worker',
	'Software Engineer',
	'Solicitor',
	'Store Assistant',
	'Student',
	'Surgeon',
	'Systems Analyst',
	'Teaching Assistant',
	'Tour Guide',
	'Train Driver',
	'Travel Agent',
	'Unemployed',
	'University Lecturer',
	'Waiter / Waitress',
	'Warehouse Operative',
	'Other'
] as const;

/** One `crm.kyc_checks` row, as the Compliance card reads it. Optional on the detail because a
 *  listing-shaped read carries none, and absent is not the same as "no checks". */
export type ComplianceCheck = {
	kind: 'identity' | 'aml' | 'terms' | string;
	status: string | null;
	verified_at?: string | null;
	valid_until?: string | null;
};

export type TermAcceptance = {
	uid: string;
	term_uid: string;
	title: string;
	code: string;
	version_number: number;
	accepted_at: string;
	channel: string;
	/** The signed copy an in-person acceptance was recorded against — a scan or photograph the
	 *  adviser uploaded. Absent on an acceptance the client clicked in the portal, where the
	 *  acknowledgement itself is the evidence. */
	evidence_document_uid?: string | null;
};

// ── Injected navigation ──────────────────────────────────────────────────────
// The shared components hardcode NO hrefs: the platform passes `/clients/{id}`-shaped builders,
// the admin portal will pass `/tenants/{uid}/book/...` ones.

// Every builder takes a **uid**, never the serial `id` — see the note on `AccountBookLinks`.

export type ClientsListingLinks = {
	/** The client record, from a listing row. */
	client: (uid: string) => string;
};

export type ClientRecordLinks = {
	/** An account's detail page, from the record's accounts table. */
	account: (uid: string) => string;
	/** Another client record, used by person-to-person relationships in Compliance. */
	client?: (uid: string) => string;
	/** The client's illustrations section (hosts the illustration form) — the FALLBACK for a mount
	 *  with no `actions.onillustrate`, which opens the form in place instead. Generating one is
	 *  `illustrations:write`, so the app passes neither without it and the header's illustration
	 *  button is hidden. */
	illustrations?: string | null;
	/** A pending review on this record — opens the Reviews queue on that case. */
	review?: (uid: string) => string;
	/** The portal's tasks queue — the "All tasks" link on the action-items card. Omit to hide. */
	tasks?: string | null;
	/** One task's detail page. Omit when the mounting app has no task-detail route in this context. */
	task?: (uid: string) => string;
	/** A document on the client's record — the signed copy behind an in-person terms acceptance.
	 *  Omit where the mounting app has no document route in this context; the acceptance still
	 *  says it was signed, just without the link. */
	document?: (uid: string) => string;
	/** One journey, read on this client's record. The overview banner uses it. */
	journey?: (uid: string) => string;
	/** The published flows this reader may start for this client, each already pointing at the
	 *  kickoff with the flow and the client chosen. Absent on a read-only mount. */
	newJourneys?: { code: string; name: string; href: string }[];
};

/** The Duration segment's value for a stored `vulnerability_is_temporary`, and back.
 *
 * The record holds THREE states — temporary, permanent, and "not established" — and the control
 * is a two-button group, so the third state is the absence of a selection. That mapping is the
 * part that breaks silently: coerce `null` to `'false'` anywhere along the way and an untouched
 * form starts asserting that every flag is permanent.
 */
export function durationChoice(isTemporary: boolean | null | undefined): string | undefined {
	return isTemporary === true || isTemporary === false ? String(isTemporary) : undefined;
}

/** The inverse, for whoever reads the posted field. `''`/absent is "not established", NOT false. */
export function durationValue(choice: string | null | undefined): boolean | null {
	return choice === 'true' ? true : choice === 'false' ? false : null;
}

/** Add or remove one driver, keeping the FCA's listed order.
 *
 * Order matters because the badge row on the record renders whatever is stored: appending would
 * make the row's order depend on the sequence the adviser happened to click in, and the API's own
 * normaliser sorts by this same listing so two records with the same drivers compare equal.
 */
export function toggleMember<T extends string>(
	current: readonly T[],
	value: T,
	on: boolean,
	order: readonly T[]
): T[] {
	const next = new Set(current);
	if (on) next.add(value);
	else next.delete(value);
	// RE-SORTED into the declared order, which is the point rather than a tidy-up: the API
	// normalises the stored array the same way, so two records with the same members compare equal
	// and a badge row does not reshuffle between saves.
	return order.filter((member) => next.has(member));
}

export function toggleReason(
	current: readonly VulnerabilityReason[],
	value: VulnerabilityReason,
	on: boolean
): VulnerabilityReason[] {
	return toggleMember(
		current,
		value,
		on,
		Object.keys(VULNERABILITY_LABELS) as VulnerabilityReason[]
	);
}

/** The same, over the 24 characteristics — in FG21/1's driver order, which is the order
 *  `app/contacts.py::VULNERABILITY_CHARACTERISTICS` normalises a stored array into. */
export function toggleCharacteristic(
	current: readonly VulnerabilityCharacteristic[],
	value: VulnerabilityCharacteristic,
	on: boolean
): VulnerabilityCharacteristic[] {
	return toggleMember(current, value, on, Object.values(CHARACTERISTICS_BY_DRIVER).flat());
}

/** The identity card's classes for a flagged client.
 *
 * The whole card is re-tinted rather than a banner being added above it: the point is that you
 * cannot read the name without reading the flag. Shared so the client record and the account
 * record tint identically — a different shade on each would read as a different severity.
 * Tokens, not raw reds, so it holds up in both schemes (`destructive-subtle` is Badge's tint).
 */
export const VULNERABLE_CARD_CLASS =
	'bg-destructive-subtle/60 border-destructive/40 ring-destructive/15 ring-1';

/** Whether a contact's fetched vulnerability block says to draw the flag.
 *
 * `undefined` is "not fetched" (the listings omit the block) and must read the same as false: a
 * surface that has not asked must not answer.
 */
export function isFlaggedVulnerable(contact: { is_vulnerable?: boolean | null }): boolean {
	return Boolean(contact.is_vulnerable);
}
