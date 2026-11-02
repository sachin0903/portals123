// The pension-illustration form, authored on the shared forms system (`@graphene/ui` DynamicForm)
// rather than hand-built markup — so it reuses the platform's field rendering, section layout,
// conditional visibility, and the section-nav rail, and every future form gets the same treatment.
//
// The bits that don't fit a plain questionnaire are expressed with the forms-system primitives:
//  • the read-only Client summary is `display` fields, seeded from the record by the host;
//  • Income shows only for a drawdown basis, Contributions only for accumulation — the same
//    gate, opposite sides: a drawdown quote has no regular contribution and an accumulation
//    quote has no withdrawals;
//  • Initial payment, Transfer, Contributions and Income are OPTIONAL sections
//    (`ILLUSTRATION_SECTIONS`), so switching one off leaves it out of the request rather than
//    sending zeros.
// Identity comes from the client record (seeded into the display fields by the host); the CTC1 body
// is shaped by `toIllustrationPayload` below, since the vendor call owns its own field names.
//
// TWO FIELDS DELIBERATELY ABSENT, both replaced by the model:
//  • "Illustrate" (which account) — the form is opened from a client, not an account, and an
//    illustration is what someone is shown BEFORE holding one. Money already in a SIPP is the
//    sum of the client's other SIPP accounts, shown above the form, not asked again.
//  • "Wrapper" — it offered SIPP as effectively the only answer. A question with one answer is
//    not a question; the payload defaults it (see PRODUCT_TYPE_DEFAULT).
// What sits in that row now is the MODEL, which is the real choice: CTC builds the whole
// projection from the model's target return, so a model without one cannot be illustrated against.

import type { ConditionOperator, FieldOptions, FieldValidation, FormField } from '@graphene/ui';
import { ageOn } from './client-panel.svelte';

/** The wrapper every illustration is built for. The form no longer asks: SIPP was the only answer
    the picker ever offered, and the engine needs a value. Change here if that stops being true. */
export const PRODUCT_TYPE_DEFAULT = 'SIPP';

/**
 * A model as the picker needs it (`GET /crm/models`).
 *
 * Both numbers are assumptions the projection is built on, not decoration: `target_return` is the
 * growth rate, and `model_charge` is the annual investment charge the engine runs it net of
 * (`FundSplit.management_charge`). Both are percentage points (6.5 = 6.5%, 0.2 = 0.2%), not
 * fractions — Seccl's `modelCharge` is a fraction and is scaled at the pipeline. They arrive as
 * numeric strings over JSON.
 */
export type IllustrationModel = {
	id: number;
	name: string;
	target_return: number | string | null;
	model_charge?: number | string | null;
};

/**
 * Per-section presentation for `<DynamicForm sectionMeta={…}>`.
 *
 * Plan is not optional — an illustration without a model is not an illustration. Adviser
 * charges is always shown too, with both fees optional: a firm that charges nothing says so by
 * leaving them blank, and "we forgot to ask" should not look the same as "there are none".
 *
 * **There is no Annuity basis section** (removed 09 Aug 2026). It asked the adviser six questions
 * about how a fund would be converted to income at retirement — a HOUSE assumption, identical for
 * every client of a firm, so putting it on a per-client form was the wrong shape from the start.
 *
 * The group is still SENT: the API applies `house_annuity_basis()` (monthly, in advance, level, no
 * guarantee, single life) when a caller supplies none. That default deliberately lives in
 * `routers/illustrations.py` rather than here, because a portal that carries the constants is a
 * portal that can forget them — and forgetting means the group is omitted, which files a KFI whose
 * projected-income column reads £0 on an accumulation quote (measured, two real PDFs off the same
 * scenario: docs/morgan-lloyd/illustrations-report.md §10; the fund columns are identical either
 * way and a drawdown quote has no income column at all).
 *
 * The API still honours an explicit basis, so a future firm-level setting is a matter of passing
 * one — see the note on `house_annuity_basis()`.
 *
 * There is no Client section either: identity is read from the record and shown as text above the
 * form, not asked for again (see `client-panel.svelte`). Retirement age and marital status are
 * the same kind of fact — shown, never re-asked, once the record holds them. A record that does
 * not hold them yet is asked here (`illustrationFieldsFor`), and generating writes the answer
 * back. It is a fill, not an edit: a value already on the record cannot be changed from this form.
 */
export const ILLUSTRATION_SECTIONS: Record<
	string,
	{ description?: string; optional?: boolean; defaultOn?: boolean }
> = {
	Plan: {
		description:
			'What is being projected, and on what. The model’s target return is the growth rate the whole illustration is built on.'
	},
	'Adviser charges': {
		description:
			'Your fees, so the projection is net of them. Leave both blank to show the projection before adviser fees.'
	},
	'Initial payment': {
		description:
			'A one-off payment into the plan. It is listed with the other payments, not as money already held.',
		optional: true,
		defaultOn: false
	},
	Transfer: {
		description:
			'Money coming in from another scheme. On a drawdown basis, say whether that transfer is crystallised.',
		optional: true,
		defaultOn: false
	},
	Contributions: {
		description: 'Regular money going in. Say who pays it. Only applies to an accumulation basis.',
		optional: true,
		defaultOn: false
	},
	Income: {
		description: 'Withdrawals taken during the projection. Only applies to a drawdown basis.',
		optional: true,
		defaultOn: false
	}
};

// The VALUE is CTC's, the LABEL is the adviser's. `Flexi Access Drawdown` is what goes into the
// XML (and into the filed document's name); "Drawdown" is what it is called here, because it is
// the only drawdown basis on offer and the qualifier only invites the question "as opposed to
// what?". CTC's own `Drawdown` and `SMPI` are dropped: capped drawdown is closed to new members,
// and an SMPI is a statutory annual statement rather than something quoted from this form.
const QUOTATION_TYPES = [
	{ value: 'Accumulation', label: 'Accumulation' },
	{ value: 'Flexi Access Drawdown', label: 'Drawdown' }
];
// `Capped` is deliberately absent (removed 09 Aug 2026). Capped drawdown has been closed to new
// members since April 2015, and offering it produced a request the engine cannot compute properly:
// it needs `current_max_income`, `next_review_date` and `inc_already_taken` (CTC1 §c, capped only)
// and we send none of the three. A picker option that silently mis-projects is worse than an
// absent one.
const DRAWDOWN_TYPES = [
	// The VALUE is CTC's `Flexible`. The LABEL is "Flexi drawdown" (the adviser's wording; the ML
	// API calls this Flexi-access drawdown) — the same basis.
	{ value: 'Flexible', label: 'Flexi drawdown' },
	{ value: 'UFPLS', label: 'UFPLS' }
];
// TWO frequency sets, because the spec has two and they are not the same list.
//
// A contribution (CTC1 §d `plan_stream`) may be Weekly; a withdrawal (§f `withdrawals`) may not —
// its set is Single/Monthly/Quarterly/Half Yearly/Annually plus `2 Months` and `4 Months`, which
// the scheme does not offer. One shared constant fed both pickers until 09 Aug 2026, so adding
// Weekly for contributions would have quietly offered it as a withdrawal frequency too.
const CONTRIBUTION_FREQUENCIES = [
	{ value: 'Weekly', label: 'Weekly' },
	{ value: 'Monthly', label: 'Monthly' },
	{ value: 'Quarterly', label: 'Quarterly' },
	{ value: 'Half Yearly', label: 'Half yearly' },
	{ value: 'Annually', label: 'Annually' }
];
// Single stays here. A withdrawal may be a one-off; a regular contribution may not — that
// one-off is the Initial payment section, its own stream, so it is not also a frequency on this
// list. Filtering Single off the contribution list must not take it off withdrawals.
const WITHDRAWAL_FREQUENCIES = [
	{ value: 'Single', label: 'Single' },
	{ value: 'Monthly', label: 'Monthly' },
	{ value: 'Quarterly', label: 'Quarterly' },
	{ value: 'Half Yearly', label: 'Half yearly' },
	{ value: 'Annually', label: 'Annually' }
];
const CONTRIBUTORS = [
	{ value: 'Personal', label: 'Personal' },
	{ value: 'Employer', label: 'Employer' }
];
/** A visibility/enable condition against another field. */
const when = (
	field: string,
	operator: ConditionOperator,
	value?: unknown
): FormField['conditions'] => ({
	conditional: { field, operator, value }
});

const onAccumulation = when('quotation_type', 'equals', 'Accumulation');
const onDrawdown = when('quotation_type', 'notEquals', 'Accumulation');

interface FieldSpec {
	section: string;
	key: string;
	type: string;
	label: string;
	options?: FieldOptions;
	validation?: FieldValidation;
	conditions?: FormField['conditions'];
	/**
	 * Marks the field as needed, which also drops the "(optional)" suffix the renderer adds to
	 * everything else.
	 *
	 * In an OPTIONAL section this reads as "required once you switch it on" — DynamicForm skips
	 * validation for a section that is off, so a required field there never blocks anything. The
	 * rule applied below: the field that makes a section mean something is required; a genuinely
	 * independent second stream inside it (an employer contribution alongside the member's, an
	 * initial fee alongside the ongoing one) stays optional, because forcing both would make the
	 * commonest case impossible to state.
	 */
	required?: boolean;
}

const field = (spec: FieldSpec): Omit<FormField, 'sortOrder'> => ({
	uid: `ill-${spec.key}`,
	fieldKey: spec.key,
	fieldType: spec.type,
	label: spec.label,
	section: spec.section,
	description: null,
	required: spec.required ?? false,
	conditions: spec.conditions ?? null,
	validation: spec.validation ?? null,
	options: spec.options ?? null
});

const YES_NO = [
	{ value: 'No', label: 'No' },
	{ value: 'Yes', label: 'Yes' }
];

const specs: FieldSpec[] = [
	// Plan — the model and the basis. The fund already held is not asked: it is the sum of the
	// client's other SIPP accounts, shown under the client details. An amount typed here used to
	// go out as an existing fund, so a transfer on top of it illustrated twice the money.
	//
	// TWO PER ROW: at a quarter of the drawer's width the Basis segmented control could not fit
	// "Accumulation" and "Drawdown" side by side.
	{
		section: 'Plan',
		key: 'model_id',
		// Searchable, not a plain select: a firm's shelf runs to dozens of models with long,
		// deliberately similar names, and scrolling a list to find "…(Sustainable, Higher Risk)" is
		// slower than typing three letters of it.
		type: 'combobox',
		label: 'Model',
		options: { source: 'models', valueKey: 'id', labelKey: 'name' },
		validation: { width: 6 },
		required: true
	},
	{
		section: 'Plan',
		key: 'quotation_type',
		type: 'segmented',
		label: 'Basis',
		options: QUOTATION_TYPES,
		validation: { width: 6, defaultValue: 'Accumulation' },
		required: true
	},

	// Adviser charges — percentages are annual rates.
	{
		section: 'Adviser charges',
		key: 'ongoing_fee',
		type: 'percentage',
		label: 'Ongoing (% pa)',
		validation: { width: 6, placeholder: '0.75' }
	},
	{
		section: 'Adviser charges',
		key: 'initial_fee',
		type: 'currency',
		label: 'Initial',
		validation: { width: 6 }
	},

	// A one-off payment, not a frequency on the regular contribution. Its own section so "no
	// initial payment" is something the adviser stated, and so the amount is required once the
	// section is on.
	{
		section: 'Initial payment',
		key: 'initial_payment',
		type: 'currency',
		label: 'Initial payment',
		validation: { width: 6, placeholder: '0.00' },
		required: true
	},

	// Transfer — its own section on purpose: a transfer in is a separate decision from the fund
	// already held. Crystallised is asked only on a drawdown basis, and only of this transfer:
	// the spec allows the flag on a transfer or an existing fund, and the existing fund is no
	// longer something the adviser types.
	{
		section: 'Transfer',
		key: 'transfer_value',
		type: 'currency',
		label: 'Transfer in',
		validation: { width: 6 },
		required: true
	},
	{
		section: 'Transfer',
		key: 'transfer_crystallised',
		type: 'segmented',
		label: 'Crystallised',
		options: YES_NO,
		validation: { width: 6, defaultValue: 'No' },
		required: true,
		conditions: onDrawdown
	},
	{
		section: 'Transfer',
		key: 'transfer_crystallised_amount',
		type: 'currency',
		label: 'Crystallised amount',
		validation: { width: 6, placeholder: '0.00' },
		required: true,
		// Only once they have said the transfer is crystallised, and only on drawdown. The amount
		// is a part of the transfer, not money on top of it.
		conditions: {
			conditional: {
				logic: 'AND',
				conditions: [
					{ field: 'quotation_type', operator: 'notEquals', value: 'Accumulation' },
					{ field: 'transfer_crystallised', operator: 'equals', value: 'Yes' }
				]
			}
		}
	},

	// Contributions on top of the existing fund. Accumulation only — the mirror of Income, which
	// shows only on a drawdown basis. A drawdown quote is about money coming OUT; asking for
	// regular money going in at the same time is a second illustration.
	//
	// ONE amount and one frequency. Who pays it is a switch, and the payload routes that single
	// amount to the member or the employer stream. The four controls sit two per row: at a quarter
	// of the drawer's width the Paid by switch and Net/gross could not sit beside Amount and
	// Frequency.
	{
		section: 'Contributions',
		key: 'contribution_amount',
		type: 'currency',
		label: 'Amount',
		validation: { width: 6, placeholder: '0.00' },
		required: true,
		conditions: onAccumulation
	},
	{
		section: 'Contributions',
		key: 'contribution_frequency',
		type: 'select',
		label: 'Frequency',
		options: CONTRIBUTION_FREQUENCIES,
		validation: { width: 6, defaultValue: 'Monthly' },
		required: true,
		conditions: onAccumulation
	},
	{
		section: 'Contributions',
		key: 'contributor',
		type: 'segmented',
		label: 'Paid by',
		options: CONTRIBUTORS,
		validation: { width: 6, defaultValue: 'Personal' },
		required: true,
		conditions: onAccumulation
	},
	{
		section: 'Contributions',
		key: 'contribution_net_gross',
		type: 'segmented',
		label: 'Net / gross',
		options: [{ value: 'Net', label: 'Net' }],
		validation: { width: 6, defaultValue: 'Net' },
		required: true,
		// Personal only, and Net only: relief at source applies to the member's own money, so the
		// question does not offer Gross. An employer contribution has no net figure to state. The
		// API agrees — there is a `member_contribution_net_gross` and no employer equivalent.
		// AND accumulation, because this field lives in Contributions and that whole section is
		// off a drawdown basis.
		conditions: {
			conditional: {
				logic: 'AND',
				conditions: [
					{ field: 'quotation_type', operator: 'equals', value: 'Accumulation' },
					{ field: 'contributor', operator: 'equals', value: 'Personal' }
				]
			}
		}
	},

	// Income — only meaningful for a drawdown basis, so it appears only then.
	//
	// Ordered as the sentence an adviser would say: how much, how often, on what basis. The
	// drawdown type led until 09 Aug 2026, which put the most technical of the three first and the
	// figure the client actually asked about third. Same reasoning as the Contributions row above.
	{
		section: 'Income',
		key: 'withdrawal_amount',
		type: 'currency',
		label: 'Withdrawal',
		validation: { width: 4 },
		required: true,
		conditions: onDrawdown
	},
	{
		section: 'Income',
		key: 'withdrawal_frequency',
		type: 'select',
		label: 'Frequency',
		options: WITHDRAWAL_FREQUENCIES,
		validation: { width: 4, defaultValue: 'Monthly' },
		required: true,
		conditions: onDrawdown
	},
	{
		section: 'Income',
		key: 'drawdown_type',
		type: 'select',
		label: 'Drawdown type',
		options: DRAWDOWN_TYPES,
		validation: { width: 4 },
		required: true,
		conditions: onDrawdown
	}
];

export const illustrationFields: FormField[] = specs.map((s, i) => ({ ...field(s), sortOrder: i }));

/** What this form will accept. The contact column still allows 50 (migration 0053) so a record
    written before the floor moved is left stored; an illustration will not project a retirement
    before the minimum pension age. */
const RETIREMENT_AGE = { min: 55, max: 75 } as const;

/**
 * The retirement age this quote writes back, or null when the record already holds one.
 *
 * The form always asks, so a typed age is not by itself a gap fill. A stored age of 55–75
 * stays; a missing age, or one under 55, is replaced by what was typed. Kickoff copies the
 * result onto the party, and must not make a local client look 60 when the record was left at 65.
 */
export function retirementAgeWrittenBack(
	recorded: number | null | undefined,
	typed: unknown
): number | null {
	const raw = String(typed ?? '').trim();
	const age = raw === '' ? Number.NaN : Number(raw);
	if (!Number.isInteger(age)) return null;
	const stands =
		recorded != null && recorded >= RETIREMENT_AGE.min && recorded <= RETIREMENT_AGE.max;
	return stands ? null : age;
}

/** Must match `DRAWDOWN_HORIZON_AGE` and `MAX_EXIT_AGE` in `xml_builder.py`. */
export const DRAWDOWN_HORIZON_AGE = 99;
const MAX_ILLUSTRATION_AGE = 100;

function isDrawdownBasis(quotationType: unknown): boolean {
	return typeof quotationType === 'string' && quotationType.includes('Drawdown');
}

/**
 * What to tell an adviser whose client is already past the recorded retirement age.
 *
 * Drawdown still illustrates: the quote runs to `DRAWDOWN_HORIZON_AGE`, because the engine
 * treats exit age as the end of the term and a past age prints a maturity date that has
 * already happened. Accumulation has no term left, so Generate is blocked.
 */
export function illustrationTerm(
	client: { dob?: string | null; retirement_age?: number | null },
	quotationType: unknown,
	now = new Date(),
	/** The retirement age typed into the form. It is what this quote uses, ahead of the record. */
	answeredAge: unknown = null
): { note: string | null; problem: string | null } {
	const recorded = client.retirement_age;
	const raw = String(answeredAge ?? '').trim();
	const typed = raw === '' ? Number.NaN : Number(raw);
	const retirement = Number.isInteger(typed) ? typed : (recorded ?? null);
	const age = ageOn(client.dob, now);
	if (retirement == null || age == null || retirement > age) return { note: null, problem: null };
	if (!isDrawdownBasis(quotationType)) {
		return {
			note: null,
			problem:
				`Retirement age ${retirement} is already past, so an accumulation illustration has no term left. ` +
				'A pension already in payment is illustrated as drawdown.'
		};
	}
	const horizon = Math.max(DRAWDOWN_HORIZON_AGE, age + 1);
	if (horizon > MAX_ILLUSTRATION_AGE) {
		const problem = `This client is already ${age}, and an illustration cannot run past age ${MAX_ILLUSTRATION_AGE}.`;
		return { note: problem, problem };
	}
	return {
		note: `Retirement age ${retirement} is already past. This drawdown illustration runs to age ${horizon}.`,
		problem: null
	};
}

/**
 * The form for THIS client.
 *
 * Retirement age is always asked, and never seeded from the record. A value already sitting in
 * the box is how an adviser submits a quote they have not actually entered — and then meets the
 * error only after the rest of the form is done. Marital status is the opposite: a status the
 * record already holds is shown as text, because an illustration must not be able to disagree
 * with it. A missing status is asked here, required, and written back when the quote is generated.
 */
export function illustrationFieldsFor(client: {
	retirement_age?: number | null;
	marital_status?: string | null;
}): FormField[] {
	const gaps: FieldSpec[] = [
		{
			section: 'Plan',
			key: 'retirement_age',
			type: 'number',
			label: 'Retirement age',
			required: true,
			validation: {
				width: 6,
				min: RETIREMENT_AGE.min,
				max: RETIREMENT_AGE.max,
				errorMessage: `Enter a retirement age between ${RETIREMENT_AGE.min} and ${RETIREMENT_AGE.max}.`
			}
		}
	];
	if (!client.marital_status) {
		gaps.push({
			section: 'Plan',
			key: 'marital_status',
			type: 'marital',
			label: 'Marital status',
			required: true,
			validation: { width: 6 }
		});
	}
	// Ahead of the plan pairs, so the facts the projection is built from are answered first.
	return [...gaps.map(field), ...illustrationFields].map((f, i) => ({ ...f, sortOrder: i }));
}

const num = (v: unknown): number | null => {
	const s = String(v ?? '').trim();
	return s === '' ? null : Number(s);
};

/** Answers the form can hold that the engine would refuse, said before the request is built.
 *  `transferOn` is the Transfer section's switch. A switched-off section keeps its typed answers
 *  in `values` (so toggling it back does not lose them) but they are not part of the request, so
 *  a leftover crystallised amount must not block Generate. */
export function illustrationAnswerProblems(
	values: Record<string, unknown>,
	options: { transferOn?: boolean } = {}
): string[] {
	if (options.transferOn === false) return [];
	if (values.quotation_type === 'Accumulation' || values.transfer_crystallised !== 'Yes') return [];
	const transfer = num(values.transfer_value);
	const crystallised = num(values.transfer_crystallised_amount);
	if (transfer == null || crystallised == null || crystallised <= transfer) return [];
	return ['The crystallised amount cannot be more than the transfer.'];
}

/** Shape the flat form values into the `POST /clients/{uid}/generate-illustration` body (the CTC1 field
    names). Always a prospective illustration: `account_id` is null and the wrapper is defaulted,
    because the form asks for neither. `existingSippTotal` is the client's other SIPP accounts,
    summed by the host — the current value CTC prints, and not a figure the adviser types.
    Display fields are ignored (they carry no answer). Pure, so it is unit-tested directly. */
export function toIllustrationPayload(
	values: Record<string, unknown>,
	existingSippTotal: number | null = null
): Record<string, unknown> {
	const isDrawdown = values.quotation_type !== 'Accumulation';
	// The form asks for one contribution and who pays it; the API keeps two streams. Route the
	// single amount to the right pair and null the other, so an adviser switching Personal →
	// Employer cannot leave a stale member contribution behind in the request.
	//
	// Drawdown nulls BOTH streams even if the values object still holds them: the Contributions
	// section is hidden on that basis, but the full-page composer posts raw `values` rather than
	// `collect()`, and a leftover amount from a previous Accumulation fill must not ride along.
	const byEmployer = !isDrawdown && values.contributor === 'Employer';
	const contribution = isDrawdown ? null : num(values.contribution_amount);
	// `values` here is what DynamicForm.collect() returned, so a switched-off section is already
	// absent — `num(undefined)` is null and the engine reads that as "no such stream". That is why
	// this does not need to know which sections were on.
	return {
		account_id: null,
		product_type: PRODUCT_TYPE_DEFAULT,
		model_id: num(values.model_id),
		// The client's other SIPPs, summed by the host. Not a form answer: an amount typed at the
		// top used to be sent as the current value, and a transfer on top of it double-counted.
		existing_fund_value: existingSippTotal ?? 0,
		crystallised_fund_value: null,
		initial_payment: num(values.initial_payment),
		transfer_value: num(values.transfer_value),
		// The crystallised part of a drawdown transfer. Hidden unless they said yes, and a
		// leftover yes from an accumulation fill must not ride along.
		transfer_crystallised_amount:
			isDrawdown && values.transfer_crystallised === 'Yes'
				? num(values.transfer_crystallised_amount)
				: null,
		quotation_type: values.quotation_type,
		// Sent only when the form asked — a gap on the record. Absent (null) means "use the record",
		// and the API refuses to let a sent value overwrite one the record already holds.
		exit_age: num(values.retirement_age),
		marital_status:
			typeof values.marital_status === 'string' && values.marital_status.trim()
				? values.marital_status.trim()
				: null,
		member_contribution: byEmployer ? null : contribution,
		member_contribution_frequency:
			byEmployer || isDrawdown ? null : (values.contribution_frequency ?? null),
		member_contribution_net_gross:
			byEmployer || isDrawdown ? null : (values.contribution_net_gross ?? null),
		employer_contribution: byEmployer ? contribution : null,
		employer_contribution_frequency: byEmployer ? (values.contribution_frequency ?? null) : null,
		ongoing_adviser_fee_percent: num(values.ongoing_fee),
		initial_adviser_fee_amount: num(values.initial_fee),
		drawdown_type: isDrawdown && values.drawdown_type ? values.drawdown_type : null,
		withdrawal_amount: isDrawdown ? num(values.withdrawal_amount) : null,
		withdrawal_frequency: isDrawdown ? values.withdrawal_frequency : null
		// No annuity keys. The form no longer asks, and the API supplies house_annuity_basis() for any
		// caller that sends none — sending explicit nulls here would look like an answer and is not
		// how the endpoint distinguishes "no preference" from "leave the group out".
	};
}
