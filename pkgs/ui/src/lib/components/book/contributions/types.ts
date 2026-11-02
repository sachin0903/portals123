/** Contribution shapes as the API returns them (`GET /contributions`).
 *
 *  These mirror `platform.contributions` plus the joined account/client/expectation fields the
 *  workbench renders — see `apps/api/app/contributions.py::_SELECT`. Unlike the transactions
 *  listing, this is real data, not fixtures.
 */

/** How an adviser classified the payment. Only the first three are contributions in the tax
 *  sense; the last two exist so a payment that landed in the queue by mistake can be disposed of. */
export type ContributionCategory =
	| 'personal_contribution'
	| 'employer_contribution'
	| 'third_party_contribution'
	| 'transfer_in'
	| 'not_a_contribution';

/** The pipeline. Mirrors the CHECK constraint in `0051_contributions_ledger.sql`, widened by
 *  `0056_contribution_lifecycle.sql` with the `cancelled` terminal.
 *
 *  `manual_intervention` and `cancelled` are OFF-pipeline: they are ways a contribution stops,
 *  not stages 9 and 10, so the pipeline strip excludes them rather than appending them. */
export type ContributionStatus =
	| 'identified'
	| 'awaiting_confirmation'
	| 'confirmed'
	| 'ml_recorded'
	| 'ras_applied'
	| 'ras_received'
	| 'tax_relief_received'
	| 'reconciled'
	| 'manual_intervention'
	| 'cancelled';

/** Morgan Lloyd's `cam_status` on the `cam_contribution` record, mirrored verbatim as the
 *  option-set integer (`morgan_lloyd.enums.ContributionStatus`).
 *
 *  This is a control, not a badge: `cam_updatecontributionapplication` is only valid at
 *  `Application`, so the workbench offers "Amend at Morgan Lloyd" on 809130000 and nothing else. */
export const ML_STATUS = {
	application: 809130000,
	active: 809130001,
	stopped: 809130002,
	received: 809130003
} as const;

export type MlContributionStatus = (typeof ML_STATUS)[keyof typeof ML_STATUS];

export const ML_STATUS_LABELS: Record<number, string> = {
	[ML_STATUS.application]: 'Application',
	[ML_STATUS.active]: 'Active',
	[ML_STATUS.stopped]: 'Stopped',
	[ML_STATUS.received]: 'Received'
};

/** The three-way compare, derived server-side (`app/matching.py::reconcile`) — booked vs
 *  forecast vs what actually landed. Not stored: a break is a statement about the current rows,
 *  so persisting it creates a copy that goes stale the moment either side is corrected. */
export interface ReconciliationLeg {
	key: 'graphene' | 'expectation' | 'custodian';
	label: string;
	value: number | null;
}

export interface Reconciliation {
	status: 'pending' | 'matched' | 'break';
	legs: ReconciliationLeg[];
	variance: number | null;
	reason: string | null;
}

/** Relief expected vs relief received, per contribution. `received` is summed at ACCOUNT grain
 *  because SEI pays relief as a monthly batch against the scheme, not per contribution — so
 *  attributing a relief credit to one contribution would be an allocation, not a fact. */
export interface ReliefPosition {
	status: 'not_applicable' | 'awaited' | 'received' | 'short';
	expected: number;
	received: number | null;
	outstanding: number;
}

/** An incoming credit with no domain record against it — `GET /contributions/unclassified`.
 *
 *  The front of path A: the client sent money, the custodian's feed landed it in
 *  `platform.transactions`, and nobody has said what it is yet. */
export interface UnclassifiedCredit {
	id: number;
	source_system: string;
	source_transaction_id: string;
	type: string;
	/** The custodian's own words. For SEI this is the TRANSACTION_DESCRIPTION ("Cash Receipt"). */
	source_type: string | null;
	/** SEI's `DESCRIPTION_CODE_ID` — what separates a contribution (21121) from the relief
	 *  payment that follows it (21122). Both are Cash Receipts on the same account. */
	source_subtype: string | null;
	amount: number;
	currency: string;
	transaction_date: string | null;
	settlement_date: string | null;
	status: string | null;
	account_id: number;
	account_uid: string | null;
	account_name: string | null;
	product_type: string | null;
	contact_id: number | null;
	contact_uid: string | null;
	client_name: string | null;
	adviser: string | null;
	/** The client's own employer (`crm.contacts.employer_name`), so the panel can say "this client
	 *  has no employer on file" BEFORE an employer classification is attempted rather than after
	 *  the API refuses it. */
	client_employer_name: string | null;
	/** True when the custodian's code says this is basic-rate relief the scheme reclaimed, not a
	 *  fresh contribution. Classifying it as one would double-count the annual allowance. */
	is_relief_payment: boolean;
	/** What the custodian's code implies. A pre-selection only — the adviser's choice is what is
	 *  stored, because the code says how the money was labelled and the HMRC decision is ours. */
	suggested_category: ContributionCategory | null;
	/** The Reference report's own label for the description code, shown as provenance. */
	provider_description: string | null;
	/** How the money arrived — see `Contribution.transaction_method`. */
	method: string | null;
}

/** A credit that could be an expectation's money — `GET /contributions/{uid}/match-candidates`.
 *  Ranked by how far out the amount is, then the date. */
export interface MatchCandidate {
	id: number;
	source_system: string;
	source_transaction_id: string;
	type: string;
	source_type: string | null;
	source_subtype: string | null;
	amount: number;
	currency: string;
	transaction_date: string | null;
	amount_delta: number;
	days_delta: number | null;
}

/** What `POST /contributions/match` reports. `ambiguous` is the interesting number — it is work
 *  for a human, and a workbench that never showed it would leave money unreconciled silently. */
export interface MatchReport {
	matched: number;
	ambiguous: number;
	unmatched: number;
	matches: { expectation_uid: string; transaction_id: number }[];
	needs_review: { expectation_uid: string; candidates: number[]; reason: string }[];
}

/** Widened from the portal's old one_off/regular binary: Morgan Lloyd's `cam_frequency` needs
 *  the actual period, so "regular" alone cannot be sent. */
export type ContributionFrequency = 'single' | 'monthly' | 'quarterly' | 'half_yearly' | 'annually';

export type PaidBy = 'client' | 'third_party' | 'employer';

/** `platform.transaction_expectations.status` — the forecast's own lifecycle, orthogonal to the
 *  contribution's. `instructed` means SEI has been told to expect the money. */
export type ExpectationStatus =
	'expected' | 'instructed' | 'matched' | 'reconciled' | 'cancelled' | 'failed';

/** The contributor block Morgan Lloyd requires for anything that is not a personal
 *  contribution. Held on the contribution, not the client — the payer of an employer
 *  contribution is not the member. */
export interface Employer {
	employer_name?: string | null;
	address_line1?: string | null;
	address_line2?: string | null;
	address_line3?: string | null;
	city?: string | null;
	county?: string | null;
	postcode?: string | null;
	phone?: string | null;
	email?: string | null;
}

export interface Contribution extends Employer {
	id: number;
	uid: string;
	account_id: number;
	contact_id: number | null;
	expectation_id: number | null;
	transaction_id: number | null;

	category: ContributionCategory;
	amount: number;
	/** Server-computed. The client's preview is a preview; this is the number of record. */
	gross_amount: number | null;
	relief_amount: number | null;
	prefunded_ras_amount: number | null;
	currency: string;
	tax_year: string | null;
	frequency: ContributionFrequency;
	paid_by: PaidBy;
	contribution_date: string | null;

	status: ContributionStatus;
	ml_contribution_id: string | null;
	ml_recorded_at: string | null;
	/** Morgan Lloyd's own `cam_status`, mirrored. Null until the contribution is pushed. */
	ml_status: MlContributionStatus | null;
	ml_status_at: string | null;
	cancelled_at: string | null;
	cancelled_reason: string | null;
	note: string | null;
	created_at: string;
	updated_at: string;

	/** Joined context. */
	account_name: string | null;
	account_uid: string | null;
	/** The client's PUBLIC handle, so a row can link to the person it names. `contact_id` above is
	 *  the serial the API's filters and joins key on; a URL never carries one. This projection had
	 *  `account_uid` and no client counterpart until Sept 2026 — the same omission `docs/api.md`
	 *  records for the transactions projection and `GET /documents/requests`, both of which linked
	 *  the serial as a result. */
	contact_uid: string | null;
	product_type: string | null;
	client_name: string | null;
	adviser: string | null;
	/** The client's own employer, off `crm.contacts.employer_name`. An employer contribution needs
	 *  one before Morgan Lloyd will take it, and it is a fact about the PERSON rather than about
	 *  one payment — so it is recorded on the client and read here, not asked per credit. */
	client_employer_name: string | null;

	expectation_uid: string | null;
	expectation_status: ExpectationStatus | null;
	expectation_due_date: string | null;
	expectation_amount: number | null;
	expectation_matched_at: string | null;
	/** SEI's activity id, once the expectation is lodged. */
	expectation_reference: string | null;
	/** Why the last lodge attempt did not land. */
	expectation_error: string | null;
	/** True when an operator matched the credit rather than the automatic matcher. NULL
	 *  `matched_by` is how a tolerance stays distinguishable from a person's decision. */
	matched_by_hand: boolean | null;

	/** The realised credit, once matched. Null on both paths until then: booked-ahead has no
	 *  transaction yet, and a credit classified from the queue has one but no expectation. */
	transaction_amount: number | null;
	transaction_date: string | null;
	transaction_currency: string | null;
	transaction_source: string | null;
	transaction_reference: string | null;
	/** The custodian's own name for the movement — "Payment", "Cash Receipt". */
	transaction_type_label: string | null;
	transaction_description_code: string | null;
	/** How the money arrived, in the custodian's words: "BACS Credit", "Direct Debit". Seccl
	 *  publishes it, SEI does not — NULL means the custodian did not say, and the provenance line
	 *  omits the segment rather than printing "unknown". */
	transaction_method: string | null;

	/** Derived server-side — see `app/matching.py`. The portal renders these, never recomputes
	 *  them: they are money arithmetic, and the client is not trusted with an HMRC number. */
	reconciliation: Reconciliation;
	relief: ReliefPosition;
	/** Basic-rate relief SEI has paid against this ACCOUNT, not this contribution. */
	relief_received: number | null;
}

/** Amount and count per stage over the WHOLE book, off `GET /contributions`'s envelope
 *  (`app/contributions.py::stage_totals`).
 *
 *  Server-computed rather than derived from the rows on screen, because the rows on screen are one
 *  page: "£38,400 is with the scheme administrator" derived from twenty-five of four hundred rows
 *  is a statement about the scroll position wearing the clothes of a book total. Computed before
 *  the filters too — the strip is how you navigate to a stage, so narrowing to one must not empty
 *  the other five. */
export interface StageTotal {
	amount: number;
	count: number;
}

export type StageSummary = Record<string, StageTotal>;

/** The `sei` block on a create response — the lodge outcome, reported rather than raised so a
 *  booked contribution is never lost to an integration problem. */
export interface LodgeReport {
	status: 'instructed' | 'blocked' | 'unavailable' | 'failed' | 'skipped';
	detail?: string;
	activity_id?: string | null;
	missing?: string[];
	warnings?: { code: string | null; message: string | null }[];
}

// ── Injected navigation ──────────────────────────────────────────────────────
// The workbench hardcodes NO hrefs: the platform passes `/clients/{uid}`, and the admin portal
// would pass `/tenants/{uid}/book/clients/{uid}`. Absent ⇒ the name renders as plain text rather
// than a dead link, which is how a read-only mount with no client route stays correct.
//
// The builder takes a **uid**, never the serial `id` — see `AccountBookLinks` for why.
export type ContributionLinks = {
	/** A client's record, from a queue row or the classify panel's header. */
	client: (uid: string) => string;
	/** An account's record, wherever one is NAMED — the queue's account line and uid chip, the
	 *  panel's sub-heading, the credit queue and the relief monitor.
	 *
	 *  OPTIONAL where `client` is required, because not every mount has an account route: the admin
	 *  read-only overlay passes neither and both names stay plain text. The row already carries
	 *  `account_uid`, so this never needs `account_id` — the serial says how many rows preceded it,
	 *  and a URL is published (bookmarked, pasted into a ticket, read out loud). */
	account?: (uid: string) => string;
};

export const CONTRIBUTION_CATEGORIES: { value: ContributionCategory; label: string }[] = [
	{ value: 'personal_contribution', label: 'Personal contribution' },
	{ value: 'employer_contribution', label: 'Employer contribution' },
	{ value: 'third_party_contribution', label: 'Third-party contribution' },
	{ value: 'transfer_in', label: 'Transfer in' },
	{ value: 'not_a_contribution', label: 'Not a contribution' }
];

/** The categories offered when BOOKING a contribution — a contribution is money paid IN by a
 * person or their employer, and nothing else.
 *
 * The full list above stays as the *classification* vocabulary for the Action-required queue,
 * which has to be able to say what an unexplained payment-in actually was. Creation is narrower
 * on purpose:
 *   - `transfer_in` is a transfer, not a contribution. It has its own lifecycle (ceding provider,
 *     discharge forms, in-specie vs cash) and lives in Transfers.
 *   - `third_party_contribution` is a payer variation, not a category — "Paid by" already
 *     records who sent the money.
 *   - `not_a_contribution` is a classification outcome; booking one would be a contradiction.
 * Both survivors are pushable to Morgan Lloyd (see UNPUSHABLE_CATEGORIES).
 */
export const CREATABLE_CONTRIBUTION_CATEGORIES: {
	value: ContributionCategory;
	label: string;
}[] = [
	{ value: 'personal_contribution', label: 'Personal' },
	{ value: 'employer_contribution', label: 'Employer' }
];

/** The outcomes that are NOT a contribution type — the complement of the creatable pair.
 *
 * The classify panel needs these and the booking drawer needs none of them, and that asymmetry is
 * the point. Booking is a deliberate act about money that has not arrived; classifying is answering
 * "what was this credit?", and the answer is sometimes "a transfer" or "not a contribution at all".
 * Without them a credit that is neither contribution type cannot be disposed of, so it resurfaces
 * in the queue for ever.
 *
 * **`third_party_contribution` is NOT offered.** It is a payer variation dressed as a category, and
 * offering it made the classification a three-way choice where the tax question is binary: is this
 * the member's money (relievable, relief at source) or the employer's (gross, no relief)? A
 * grandparent paying into a grandchild's SIPP is the MEMBER'S contribution for relief purposes and
 * is classified `personal_contribution` — who moved the money is a fact about the payment, not
 * about what HMRC is being told. The value stays in `ContributionCategory`, in
 * `CONTRIBUTION_CATEGORIES` and in the 0051 CHECK, so rows classified that way before Sept 2026
 * still read back, still render and still push: this narrows what can be CHOSEN, not what exists.
 *
 * `not_a_contribution` is written AND cancelled in the same call (`app/contributions.py`), which is
 * what stops the credit coming back.
 */
export const OTHER_CONTRIBUTION_OUTCOMES: {
	value: ContributionCategory;
	label: string;
}[] = [
	{ value: 'transfer_in', label: 'Transfer in' },
	{ value: 'not_a_contribution', label: 'Not a contribution' }
];

export const IS_OTHER_OUTCOME: ReadonlySet<ContributionCategory> = new Set(
	OTHER_CONTRIBUTION_OUTCOMES.map((o) => o.value)
);

export const FREQUENCIES: { value: ContributionFrequency; label: string }[] = [
	// "Ad-hoc", not "Single", so a contribution reads back in the same words it was recorded in —
	// the forms ask ad-hoc-or-regular (`PAYMENT_TYPES`) and this is the only label that renders a
	// stored frequency. The WIRE value is untouched: the database and `cam_frequency` know `single`.
	{ value: 'single', label: 'Ad-hoc' },
	{ value: 'monthly', label: 'Monthly' },
	{ value: 'quarterly', label: 'Quarterly' },
	{ value: 'half_yearly', label: 'Half-yearly' },
	{ value: 'annually', label: 'Annually' }
];

/** How the form asks the question: is this a one-off or does it recur?
 *
 * Presentation only — there is no `payment_type` on the wire. Ad-hoc resolves to the `single`
 * frequency and Regular resolves to whichever period is then chosen, so both arrive as a plain
 * `ContributionFrequency`. Morgan Lloyd's `cam_frequency` needs the actual period, so "Regular"
 * on its own is never a recordable value — which is exactly why picking one is a second step
 * rather than an option in the same group.
 */
export const PAYMENT_TYPES = [
	{ value: 'adhoc', label: 'Ad-hoc' },
	{ value: 'regular', label: 'Regular' }
] as const;

export type PaymentType = (typeof PAYMENT_TYPES)[number]['value'];

/** The frequency shown once Regular is chosen — every period except the one-off. */
export const REGULAR_FREQUENCIES = FREQUENCIES.filter((f) => f.value !== 'single');

/** The wire value for a payment type. Ad-hoc IS `single`; Regular carries its own period. */
export const frequencyFor = (
	paymentType: PaymentType,
	regular: ContributionFrequency
): ContributionFrequency => (paymentType === 'adhoc' ? 'single' : regular);

export const PAID_BY_OPTIONS: { value: PaidBy; label: string }[] = [
	{ value: 'client', label: 'Client' },
	{ value: 'third_party', label: 'Third party' },
	{ value: 'employer', label: 'Employer' }
];

/** Categories Morgan Lloyd has no concept of, so they are never pushed. Mirrors
 *  `app/integrations/morgan_lloyd/contributions.py::UNPUSHABLE`. */
export const UNPUSHABLE_CATEGORIES: ReadonlySet<ContributionCategory> = new Set([
	'transfer_in',
	'not_a_contribution'
]);

/** Categories that require an employer/other contributor name before they can be recorded. */
export const NEEDS_EMPLOYER: ReadonlySet<ContributionCategory> = new Set(['employer_contribution']);

/** Categories where Morgan Lloyd wants the CONTRIBUTOR's details — a superset of the above, because
 *  the payer of a third-party contribution is not the member either.
 *
 *  NO LONGER DRIVES A FORM. The classify panel's contributor block is gone (the employer is the
 *  client's own fact, on their record), and `third_party_contribution` is no longer offered as a
 *  classification. Kept because the DISTINCTION is still true of stored rows and of what
 *  `cam_createcontribution` asks for, and because `contributions_employer_named` (0051) is written
 *  against exactly the `NEEDS_EMPLOYER` half of it. */
export const NEEDS_CONTRIBUTOR: ReadonlySet<ContributionCategory> = new Set([
	'employer_contribution',
	'third_party_contribution'
]);

/** The payer implied by the category. Asked as its own question until Aug 2026, which let the two
 *  disagree — a personal contribution recorded as paid by an employer. `paid_by` is still on the
 *  wire because Morgan Lloyd needs it; it is derived here instead of collected. */
export const paidByFor = (category: ContributionCategory): PaidBy =>
	category === 'employer_contribution'
		? 'employer'
		: category === 'third_party_contribution'
			? 'third_party'
			: 'client';

/** The payment type a stored frequency came from — the inverse of `frequencyFor`, for loading a
 *  record back into the two controls. */
export const paymentTypeFor = (frequency: ContributionFrequency): PaymentType =>
	frequency === 'single' ? 'adhoc' : 'regular';
