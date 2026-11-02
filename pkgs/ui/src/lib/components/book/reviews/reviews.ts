import type { StatusTone } from '../../ui/status-badge/index.js';
import { formatGBP } from '../../../money.js';
import { formatDate } from '../../../dates.js';
import { countryName } from '../../../countries.js';
import {
	EMPLOYMENT_OPTIONS,
	GENDER_OPTIONS,
	MARITAL_OPTIONS,
	TITLE_OPTIONS,
	type ClosedOption
} from '../../../forms/vocabularies.js';
import { bankDisplayRows } from '../clients/bank.js';
import { fatcaFormLabel } from '../clients/person-facts.js';
import {
	TAX_RELIEF_LABELS,
	VULNERABILITY_CHARACTERISTIC_LABELS,
	VULNERABILITY_LABELS
} from '../clients/types.js';
import type { BankDetails } from '../clients/types.js';
import { ALL, humanise, matches, search } from '../listing/filters.js';
import type { ReviewRow, ReviewScope, ReviewSeat, ReviewStatus } from './types.js';

export const REVIEW_SCOPE_LABELS: Record<ReviewScope, string> = {
	inbox: 'Awaiting my review',
	mine: 'Submitted by me',
	all: 'All'
};

export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
	pending: 'Waiting',
	approved: 'Approved',
	rejected: 'Rejected',
	cancelled: 'Cancelled',
	conflicted: 'Conflicted'
};

export const REVIEW_STATUS_TONES: Record<ReviewStatus, StatusTone> = {
	pending: 'warning',
	approved: 'success',
	rejected: 'danger',
	cancelled: 'neutral',
	conflicted: 'warning'
};

const BANK_KEYS = new Set(['contact.bank_details_changed', 'account.bank_details_changed']);

export function isBankReview(row: Pick<ReviewRow, 'action_key'>): boolean {
	return BANK_KEYS.has(row.action_key);
}

function asRecord(value: unknown): Record<string, unknown> | null {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
	return value as Record<string, unknown>;
}

function asBank(value: unknown): BankDetails | null {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
	return value as BankDetails;
}

function firstBank(value: unknown): BankDetails | null {
	if (Array.isArray(value)) return asBank(value[0] ?? null);
	return asBank(value);
}

const BANK_FIELD_KEYS = [
	'name',
	'bank',
	'sort_code',
	'account_number',
	'iban',
	'bic',
	'method',
	'scheme'
] as const;

/** Proposed bank object. The contact door dumps fields on the payload; the account door nests them. */
export function reviewBankProposed(row: Pick<ReviewRow, 'payload'>): BankDetails | null {
	const payload = row.payload;
	if (!payload) return null;
	const nested = firstBank(payload.bank_account ?? payload.bank_accounts);
	if (nested) return nested;
	if (BANK_FIELD_KEYS.some((key) => payload[key] != null && payload[key] !== '')) {
		return asBank(payload);
	}
	return null;
}

/** Spoken case handle. The uid stays in the URL; staff say this on the phone. */
export function reviewCaseRef(caseNo: number | null | undefined): string {
	if (caseNo == null || !Number.isFinite(caseNo) || caseNo < 1) return 'RV-—';
	return `RV-${caseNo}`;
}

export function reviewSubmittedAt(iso: string): string {
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return iso;
	return date.toLocaleString('en-GB', {
		day: 'numeric',
		month: 'short',
		year: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});
}

export function reviewChangeLabel(value: string | null | undefined): string | null {
	if (!value || value === 'None' || value === 'No bank details') return null;
	return value;
}

/** `changed` is set when the diff can tell: a form that resends its whole card lists rows that
 *  do not move, and the body shows those muted so the eye lands on the ones that do. */
export type ReviewFieldDiff = { field: string; now: string; proposed: string; changed?: boolean };

/** `money` carries the figure so it can render split pounds/pence; `value` stays the text. */
export type ReviewFact = { label: string; value: string; money?: { amount: number } };

export type ReviewChangeView =
	| { kind: 'diff'; title: string; rows: ReviewFieldDiff[] }
	| { kind: 'facts'; title: string; caption: string | null; rows: ReviewFact[] };

/** The kind an instruction snapshot names (a held payment's `input.kind`), as the register says it. */
const INSTRUCTION_KIND_LABELS: Record<string, string> = {
	expected_in: 'Expected in',
	withdrawal: 'Withdrawal',
	adhoc_fee: 'Ad hoc fee',
	model_topup: 'Model top-up',
	model_withdrawal: 'Model withdrawal'
};

export function instructionKindLabel(kind: string | null | undefined): string {
	if (!kind) return 'Instruction';
	return INSTRUCTION_KIND_LABELS[kind] ?? humanise(kind);
}

/** Keys of a recorded input whose value is money, printed split pounds/pence. */
const MONEY_INPUT_KEYS = new Set([
	'amount',
	'adviser_fee',
	'total_value',
	'crystallised_value',
	'uncrystallised_value'
]);
const DATE_INPUT_KEYS = new Set(['due_date', 'expected_date']);
/** Keys whose value is one of a fixed vocabulary (`in_specie`, `uncrystallised`), read as words. */
const ENUM_INPUT_KEYS = new Set([
	'basis',
	'extent',
	'crystallisation',
	'transfer_type',
	'category',
	'payment_method'
]);
/** The form's own labels for the keys the doors record; anything else is sentence-cased. */
const INPUT_LABELS: Record<string, string> = {
	client_name: 'Client',
	account_name: 'Account',
	account_uid: 'Account id',
	kind: 'Kind',
	amount: 'Amount',
	adviser_fee: 'Adviser fee',
	auto_disinvest: 'Auto-disinvest',
	due_date: 'Due',
	category: 'Category',
	payment_method: 'Payment method',
	ceding_provider: 'Ceding provider',
	policy_number: 'Policy number',
	transfer_type: 'Transfer type',
	basis: 'Basis',
	extent: 'Extent',
	crystallisation: 'Crystallisation',
	pension_type: 'Pension type code',
	pension_type_label: 'Pension type',
	safeguarded_benefits: 'Safeguarded benefits',
	crystallised_value: 'Crystallised value',
	uncrystallised_value: 'Uncrystallised value',
	total_value: 'Total value',
	currency: 'Currency',
	expected_date: 'Expected',
	note: 'Note',
	provider_address_line1: 'Scheme address',
	provider_address_line2: 'Address line 2',
	provider_city: 'City',
	provider_postcode: 'Postcode',
	provider_country: 'Country',
	fca_reference: 'FCA reference',
	external_account_type: 'Ceding account type',
	assets: 'Assets',
	documents: 'Documents'
};

function inputValueText(key: string, raw: unknown): string {
	if (raw == null || raw === '') return '—';
	if (typeof raw === 'boolean') return raw ? 'Yes' : 'No';
	if (Array.isArray(raw)) {
		const items = raw.map((item) => inputValueText(key, item)).filter((text) => text !== '—');
		return items.length ? items.join('; ') : '—';
	}
	if (typeof raw === 'object') {
		const pairs = Object.entries(raw as Record<string, unknown>)
			.filter(([, value]) => value != null && value !== '')
			.map(([k, value]) => `${humanise(k)}: ${inputValueText(k, value)}`);
		return pairs.length ? pairs.join(' · ') : '—';
	}
	if (typeof raw === 'string') return ENUM_INPUT_KEYS.has(key) ? humanise(raw) : raw;
	return String(raw);
}

/**
 * A provider action's body: the input the door recorded, one fact per field, in the order the
 * door wrote them. Money keys render split pounds/pence, date keys as dates, a vocabulary word
 * as words, a list of lines (documents, assets) as one line each. A key the form has no label
 * for is sentence-cased.
 */
/** Keys of a recorded input (or a retired review's command) that are plumbing, not facts: the
 *  ids the apply needs, the actor, the reason that already has its own column. */
function isPlumbingKey(key: string): boolean {
	return /(^|_)(id|uid)$/.test(key) || key === 'created_by_workos' || key === 'justification';
}

export function reviewInputFacts(input: Record<string, unknown> | null | undefined): ReviewFact[] {
	if (!input) return [];
	const facts: ReviewFact[] = [];
	for (const [key, raw] of Object.entries(input)) {
		if (raw == null || raw === '' || isPlumbingKey(key)) continue;
		const label = INPUT_LABELS[key] ?? humanise(key);
		if (key === 'kind' && typeof raw === 'string') {
			facts.push({ label, value: instructionKindLabel(raw) });
			continue;
		}
		if (MONEY_INPUT_KEYS.has(key) && typeof raw === 'number' && Number.isFinite(raw)) {
			facts.push({ label, value: formatGBP(raw), money: { amount: raw } });
			continue;
		}
		if (DATE_INPUT_KEYS.has(key) && typeof raw === 'string') {
			facts.push({ label, value: formatDate(raw, raw) });
			continue;
		}
		facts.push({ label, value: inputValueText(key, raw) });
	}
	return facts;
}

/** The figure a held action is about, when its input carries one: the amount, else the total. */
function inputMoney(input: Record<string, unknown> | null | undefined): number | null {
	if (!input) return null;
	for (const key of ['amount', 'total_value']) {
		const value = input[key];
		if (typeof value === 'number' && Number.isFinite(value)) return value;
	}
	return null;
}

/** A held provider action — a payment, a transfer, an account opening — rather than a book review.
 *  Its body is the recorded input, and its decision posts to the run. */
export function isActionReview(row: Pick<ReviewRow, 'source'>): boolean {
	return row.source === 'action';
}

/** Distinct actions on the loaded queue, named, for the filter bar — so the menu offers exactly
 *  the kinds that can be held here, review or provider action, and never a stale hard-coded list. */
export function reviewActionOptions(
	rows: Pick<ReviewRow, 'action_key' | 'label'>[]
): { value: string; label: string }[] {
	const seen = new Map<string, string>();
	for (const row of rows) {
		if (!row.action_key || seen.has(row.action_key)) continue;
		seen.set(row.action_key, row.label?.trim() || humanise(row.action_key));
	}
	return [...seen.entries()]
		.sort((a, b) => a[1].localeCompare(b[1], 'en-GB', { sensitivity: 'base' }))
		.map(([value, label]) => ({ value, label }));
}

// ---- Client details: one key, every card ---------------------------------------------------

/** The record's own labels for the contact columns a details review can change. */
const DETAIL_LABELS: Record<string, string> = {
	type: 'Type',
	service_type: 'Service type',
	sub_type: 'Sub-type',
	title: 'Title',
	first_name: 'First name',
	middle_name: 'Middle name',
	surname: 'Surname',
	name: 'Name',
	email: 'Email',
	mobile: 'Mobile',
	phone: 'Phone',
	address: 'Address',
	dob: 'Date of birth',
	gender: 'Gender',
	occupation: 'Occupation',
	occupation_detail: 'Occupation detail',
	country_of_birth: 'Country of birth',
	town_of_birth: 'Town of birth',
	nationality: 'Nationalities',
	national_identifier: 'National identifier',
	national_identifier_type: 'Identifier type',
	tax_residency: 'Tax residency',
	passport_number: 'Passport number',
	passport_country: 'Passport country',
	marital_status: 'Marital status',
	employment_status: 'Employment status',
	retirement_age: 'Retirement age',
	relevant_uk_earnings: 'Annual earnings',
	employer_contributions: 'Employer contributions',
	tax_relief: 'Tax relief declared',
	flexi_access_taken: 'Flexibly accessed benefits',
	flexi_access_date: 'Date accessed',
	employer_name: 'Company name',
	employer_registration_number: 'Company registration number',
	employer_address: 'Employer address',
	employer_bank_account: 'Employer bank account',
	us_person: 'US person',
	fatca_status: 'W-9 or W-8',
	fatca_certified_at: 'FATCA certified',
	source_of_funds: 'Source of funds',
	source_of_funds_detail: 'Source of funds detail',
	source_of_wealth: 'Source of wealth',
	source_of_wealth_detail: 'Source of wealth detail',
	enabled: 'Enabled'
};
const DETAIL_DATE_KEYS = new Set(['dob', 'flexi_access_date', 'fatca_certified_at']);
const DETAIL_COUNTRY_KEYS = new Set([
	'country_of_birth',
	'passport_country',
	'nationality',
	'tax_residency'
]);
const DETAIL_ADDRESS_KEYS = new Set(['address', 'employer_address']);
const DETAIL_BANK_KEYS = new Set(['employer_bank_account']);

/** A stored bank account on one line, the way the register prints its rows. */
function storedBankLine(value: unknown): string {
	const bank = asBank(value);
	if (!bank) return '—';
	const parts = bankDisplayRows(bank)
		.filter(([label, text]) => text && label !== 'Payment method')
		.map(([label, text]) => (label === 'Account name' ? String(text) : `${label} ${text}`));
	return parts.length ? parts.join(' · ') : '—';
}
const DETAIL_MONEY_KEYS = new Set(['relevant_uk_earnings']);
const DETAIL_WORD_KEYS = new Set(['type', 'service_type', 'sub_type', 'national_identifier_type']);
const DETAIL_CLOSED: Record<string, readonly ClosedOption[]> = {
	title: TITLE_OPTIONS,
	gender: GENDER_OPTIONS,
	marital_status: MARITAL_OPTIONS,
	employment_status: EMPLOYMENT_OPTIONS
};

export function detailFieldLabel(key: string): string {
	return DETAIL_LABELS[key] ?? humanise(key);
}

/** A stored address (`crm.contacts.address` / `employer_address`) on one line, the way the
 *  record prints it: lines, town, postcode, the country by name. */
function storedAddressLine(value: unknown): string {
	const row = asRecord(value);
	if (!row) return '—';
	const code = String(row.country ?? '').trim();
	const country = code.length === 2 ? countryName(code) || code : code;
	const line = [row.line1, row.line2, row.city ?? row.town, row.county, row.postcode, country]
		.map((part) => String(part ?? '').trim())
		.filter(Boolean)
		.join(', ');
	return line || '—';
}

/** One contact field as the record shows it: a date as a date, a code as its name, a closed
 *  vocabulary as its label, a yes/no as a word, money as money. */
export function detailValueText(key: string, raw: unknown): string {
	if (raw == null || raw === '') return '—';
	if (DETAIL_ADDRESS_KEYS.has(key)) return storedAddressLine(raw);
	if (DETAIL_BANK_KEYS.has(key)) return storedBankLine(raw);
	if (typeof raw === 'boolean') return raw ? 'Yes' : 'No';
	if (DETAIL_DATE_KEYS.has(key) && typeof raw === 'string') return formatDate(raw, raw);
	// The snapshot is JSON-normalised with `default=str`, so a NUMERIC column arrives as
	// "42000.00" while the proposal is a number: read both as the figure.
	const figure =
		typeof raw === 'number'
			? raw
			: typeof raw === 'string' && /^-?\d+(\.\d+)?$/.test(raw.trim())
				? Number(raw)
				: null;
	if (DETAIL_MONEY_KEYS.has(key) && figure != null && Number.isFinite(figure)) {
		return formatGBP(figure);
	}
	if (DETAIL_COUNTRY_KEYS.has(key)) {
		const codes = Array.isArray(raw) ? raw : [raw];
		const names = codes
			.map((code) => String(code ?? '').trim())
			.filter(Boolean)
			.map((code) => countryName(code) || code);
		return names.length ? names.join(', ') : '—';
	}
	const closed = DETAIL_CLOSED[key];
	if (closed && typeof raw === 'string') {
		return closed.find((option) => option.value === raw)?.label ?? humanise(raw);
	}
	if (key === 'tax_relief' && typeof raw === 'string') {
		return (TAX_RELIEF_LABELS as Record<string, string>)[raw] ?? humanise(raw);
	}
	if (key === 'fatca_status' && typeof raw === 'string') return fatcaFormLabel(raw);
	if (Array.isArray(raw)) {
		const items = raw.map((item) => detailValueText(key, item)).filter((text) => text !== '—');
		return items.length ? items.join(', ') : '—';
	}
	if (typeof raw === 'object') return inputValueText(key, raw);
	if (typeof raw === 'string') return DETAIL_WORD_KEYS.has(key) ? humanise(raw) : raw;
	return String(raw);
}

export function isDetailsReview(row: Pick<ReviewRow, 'action_key'>): boolean {
	return row.action_key === 'client.details_changed';
}

/** Now vs proposed for every field the edit touches, in the order the card sent them. The
 *  snapshot holds the same keys, so a cleared field reads "—" on the proposed side. */
export function reviewDetailsDiff(row: Pick<ReviewRow, 'payload' | 'before'>): ReviewFieldDiff[] {
	const fields = asRecord(asRecord(row.payload)?.fields) ?? {};
	const before = asRecord(row.before) ?? {};
	return Object.keys(fields).map((key) => {
		const now = detailValueText(key, before[key]);
		const proposed = detailValueText(key, fields[key]);
		return { field: detailFieldLabel(key), now, proposed, changed: now !== proposed };
	});
}

/** The fields a details edit actually moves — the whole card when the snapshot cannot tell. */
function detailsChangedLabels(row: Pick<ReviewRow, 'payload' | 'before'>): string[] {
	const rows = reviewDetailsDiff(row);
	const moved = rows.filter((line) => line.changed);
	return (moved.length ? moved : rows).map((line) => line.field);
}

// ---- Vulnerability --------------------------------------------------------------------------

export function isVulnerabilityReview(row: Pick<ReviewRow, 'action_key'>): boolean {
	return row.action_key === 'client.vulnerability_updated';
}

function yesNo(value: unknown): string {
	if (value == null) return '—';
	return value ? 'Yes' : 'No';
}

function labelled(values: unknown, labels: Record<string, string>): string {
	if (!Array.isArray(values) || !values.length) return '—';
	return values.map((value) => labels[String(value)] ?? humanise(String(value))).join(' · ');
}

function duration(temporary: unknown): string {
	if (temporary === true) return 'Temporary';
	if (temporary === false) return 'Permanent';
	return '—';
}

/** The vulnerability card's own rows, now vs proposed. The snapshot uses the column names, the
 *  proposal the form's; both read the same way. Characteristics only when either side has any. */
export function reviewVulnerabilityDiff(
	row: Pick<ReviewRow, 'payload' | 'before'>
): ReviewFieldDiff[] {
	const before = asRecord(row.before) ?? {};
	const next = asRecord(asRecord(row.payload)?.vulnerability) ?? {};
	const reasons = VULNERABILITY_LABELS as Record<string, string>;
	const characteristics = VULNERABILITY_CHARACTERISTIC_LABELS as Record<string, string>;
	const rows: ReviewFieldDiff[] = [
		{ field: 'Vulnerable', now: yesNo(before.is_vulnerable), proposed: yesNo(next.is_vulnerable) },
		{
			field: 'Drivers',
			now: labelled(before.vulnerability_reasons, reasons),
			proposed: labelled(next.reasons, reasons)
		}
	];
	const nowCharacteristics = labelled(before.vulnerability_characteristics, characteristics);
	const nextCharacteristics = labelled(next.characteristics, characteristics);
	if (nowCharacteristics !== '—' || nextCharacteristics !== '—') {
		rows.push({ field: 'Characteristics', now: nowCharacteristics, proposed: nextCharacteristics });
	}
	rows.push(
		{
			field: 'Duration',
			now: duration(before.vulnerability_is_temporary),
			proposed: duration(next.is_temporary)
		},
		{
			field: 'Notes',
			now:
				typeof before.vulnerability_notes === 'string' && before.vulnerability_notes.trim()
					? before.vulnerability_notes
					: '—',
			proposed: typeof next.notes === 'string' && next.notes.trim() ? next.notes : '—'
		}
	);
	return rows;
}

// ---- Fee packages ---------------------------------------------------------------------------

export function isFeePackagesReview(row: Pick<ReviewRow, 'action_key'>): boolean {
	return row.action_key === 'account.fee_packages_changed';
}

/** The packages by name on both sides. The snapshot carries `names` for every id in play; an
 *  older hold without them falls back to a count. */
export function reviewFeePackagesDiff(
	row: Pick<ReviewRow, 'payload' | 'before'>
): ReviewFieldDiff[] {
	const before = asRecord(row.before) ?? {};
	const names = asRecord(before.names) ?? {};
	const said = (ids: unknown): string => {
		if (!Array.isArray(ids) || !ids.length) return 'None';
		const named = ids.map((id) => names[String(id)]);
		if (named.every((name) => typeof name === 'string' && name)) return named.join(', ');
		return `${ids.length} package${ids.length === 1 ? '' : 's'}`;
	};
	return [
		{
			field: 'Fee packages',
			now: said(before.fee_package_ids),
			proposed: said(asRecord(row.payload)?.fee_package_ids)
		}
	];
}

/** "Not vulnerable" from "Not vulnerable · 2 reasons": the describer's headline without its tail. */
function headline(value: string | null | undefined): string | null {
	const text = (value ?? '').split(' · ')[0]?.trim();
	return text ? text : null;
}

export function reviewChangeTitle(row: ReviewRow): string {
	if (isActionReview(row)) {
		const money = inputMoney(row.input);
		return money != null ? `${row.label} · ${formatGBP(money)}` : row.label;
	}
	if (row.action_key === 'account.model_changed') {
		const now = reviewChangeLabel(row.before_label);
		const next = reviewChangeLabel(row.after_label);
		if (now && next) return `${now} → ${next}`;
		return row.summary?.trim() || row.label;
	}
	if (isDetailsReview(row)) {
		const names = detailsChangedLabels(row);
		return names.length ? `Client details · ${names.join(', ')}` : 'Client details';
	}
	if (isVulnerabilityReview(row)) {
		const now = headline(row.before_label);
		const next = headline(row.after_label);
		return now && next ? `Vulnerability · ${now} → ${next}` : 'Vulnerability';
	}
	if (isFeePackagesReview(row)) {
		const now = row.before_label?.trim();
		const next = row.after_label?.trim();
		return now && next ? `Fee packages · ${now} → ${next}` : 'Fee packages';
	}
	return row.label;
}

export function reviewChangeView(row: ReviewRow): ReviewChangeView {
	if (isActionReview(row)) {
		// No now vs proposed: nothing on the book changes until the provider accepts. The body is
		// what was asked for, and the caption says why it is sitting here.
		return {
			kind: 'facts',
			title: reviewChangeTitle(row),
			caption: row.status === 'pending' ? 'Waiting for approval before this is sent.' : null,
			rows: reviewInputFacts(row.input)
		};
	}
	if (isBankReview(row)) {
		return { kind: 'diff', title: 'Bank details', rows: reviewFieldDiff(row) };
	}
	if (row.action_key === 'account.model_changed') {
		return {
			kind: 'diff',
			title: 'Model mandate',
			rows: [
				{
					field: 'Model',
					now: reviewChangeLabel(row.before_label) ?? '—',
					proposed: reviewChangeLabel(row.after_label) ?? '—'
				}
			]
		};
	}
	if (isDetailsReview(row)) {
		return { kind: 'diff', title: 'Client details', rows: reviewDetailsDiff(row) };
	}
	if (isVulnerabilityReview(row)) {
		return { kind: 'diff', title: 'Vulnerability', rows: reviewVulnerabilityDiff(row) };
	}
	if (isFeePackagesReview(row)) {
		return { kind: 'diff', title: 'Fee packages', rows: reviewFeePackagesDiff(row) };
	}
	// A retired kind on settled history (no describer any more, but the command it carried is
	// still on the row): the command's own facts, so the case still says what it was.
	const command = asRecord(row.payload);
	if (!row.before_label?.trim() && !row.after_label?.trim() && command) {
		return {
			kind: 'facts',
			title: row.summary?.trim() || row.label,
			caption: null,
			rows: reviewInputFacts(command)
		};
	}
	// Any other book review: the describer's two sentences, still as now vs proposed, so every
	// held change on the book reads the same way.
	return {
		kind: 'diff',
		title: row.summary?.trim() || row.label,
		rows: [
			{
				field: 'Change',
				now: row.before_label?.trim() || '—',
				proposed: row.after_label?.trim() || '—'
			}
		]
	};
}

export function reviewFieldDiff(row: ReviewRow): ReviewFieldDiff[] {
	const proposed = reviewBankProposed(row);
	const current = reviewBankCurrent(row);
	if (proposed || current) {
		const nowRows = current ? reviewBankRows(current) : [];
		const nextRows = proposed ? reviewBankRows(proposed) : [];
		const labels = [
			...new Set([...nowRows.map(([label]) => label), ...nextRows.map(([label]) => label)])
		];
		const nowMap = Object.fromEntries(nowRows);
		const nextMap = Object.fromEntries(nextRows);
		return labels.map((field) => ({
			field,
			now: nowMap[field] ?? '—',
			proposed: nextMap[field] ?? '—'
		}));
	}
	return [
		{
			field: 'Change',
			now: reviewChangeLabel(row.before_label) ?? '—',
			proposed: reviewChangeLabel(row.after_label) ?? '—'
		}
	];
}

export function reviewBankCurrent(row: Pick<ReviewRow, 'before'>): BankDetails | null {
	const before = row.before;
	if (!before) return null;
	return firstBank(before.value);
}

/** The same register rows the client and account cards print. */
export function reviewBankRows(b: BankDetails): [string, string | null][] {
	return bankDisplayRows(b);
}

export function isPending(row: Pick<ReviewRow, 'status'>): boolean {
	return row.status === 'pending';
}

/** Distinct submitters on the loaded queue, named for the filter bar. */
export function reviewSubmitters(
	rows: Pick<ReviewRow, 'proposed_by' | 'proposed_by_name'>[]
): { value: string; label: string }[] {
	const seen = new Map<string, string>();
	for (const row of rows) {
		if (!row.proposed_by || seen.has(row.proposed_by)) continue;
		seen.set(row.proposed_by, row.proposed_by_name?.trim() || 'Unknown');
	}
	return [...seen.entries()]
		.sort((a, b) => a[1].localeCompare(b[1], 'en-GB', { numeric: true, sensitivity: 'base' }))
		.map(([value, label]) => ({ value, label }));
}

/**
 * The status filter's explicit "everything, settled too".
 *
 * An UNSET status shows the QUEUE: reviews still waiting for a decision, and nothing else. An
 * approved change is already on its record, a rejected or cancelled one applied nothing, and a
 * conflicted one was refused at approve time; none of them can be acted on here, and together
 * they pushed the open handful below the fold. Each is one choice away: its own status, or
 * this value for the whole history.
 */
export const REVIEW_STATUS_ALL = 'all';

/** What the unset status row is called, so the resting filter says what it is doing. */
export const REVIEW_STATUS_UNSET_LABEL = 'Waiting for a decision';

export function reviewStatusOptions(): { value: string; label: string }[] {
	// The unset row IS "Waiting", so the status itself is not repeated as a choice.
	const statuses: ReviewStatus[] = ['approved', 'rejected', 'cancelled', 'conflicted'];
	return [
		...statuses.map((value) => ({ value, label: REVIEW_STATUS_LABELS[value] })),
		{ value: REVIEW_STATUS_ALL, label: 'All statuses' }
	];
}

export function reviewStatusMatches(filter: string, status: ReviewStatus): boolean {
	if (!filter) return status === 'pending';
	if (filter === REVIEW_STATUS_ALL) return true;
	return status === filter;
}

/** True when the default view is hiding settled rows that exist on the loaded queue. */
export function hidesSettled(
	rows: Pick<ReviewRow, 'status'>[],
	filters: { status: string }
): boolean {
	return !filters.status && rows.some((row) => row.status !== 'pending');
}

/**
 * The rows the listing shows: the filtered queue, plus the case a deep link opened even when
 * the filters would hide it. `/reviews?open=<uid>` from an approval notification points at an
 * approved case; a page that hid the very row it was asked to open would read as a broken link.
 */
export function visibleReviews(
	rows: ReviewRow[],
	filters: {
		submitted_by: string[];
		action_key: string;
		status: string;
		q?: string;
		seat?: ReviewSeat | string | null;
	},
	openUid: string | null | undefined
): ReviewRow[] {
	const shown = matchingReviews(rows, filters);
	if (!openUid || shown.some((row) => row.uid === openUid)) return shown;
	const opened = rows.find((row) => row.uid === openUid);
	return opened ? [opened, ...shown] : shown;
}

/**
 * Status, as the seat and the Status filter together ask for it. With no seat the older rule
 * holds (unset = waiting, `all` = everything, else that status). Under a seat the seat decides
 * what is waiting and what is settled, and the Status filter only narrows the Settled seat.
 */
function seatStatusMatches(
	seat: ReviewSeat | string | null | undefined,
	status: string,
	row: ReviewRow
): boolean {
	const chosen = reviewSeatOf(seat);
	if (!chosen) return reviewStatusMatches(status, row.status);
	if (!seatMatches(row, chosen)) return false;
	if (chosen !== 'settled' || !status || status === REVIEW_STATUS_ALL) return true;
	return row.status === status;
}

export function matchingReviews(
	rows: ReviewRow[],
	filters: {
		submitted_by: string[];
		action_key: string;
		status: string;
		q?: string;
		seat?: ReviewSeat | string | null;
	}
): ReviewRow[] {
	return rows.filter(
		(row) =>
			matches(filters.submitted_by, row.proposed_by) &&
			matches(filters.action_key || ALL, row.action_key) &&
			seatStatusMatches(filters.seat, filters.status, row) &&
			search(
				filters.q ?? '',
				row.label,
				row.summary,
				row.contact_name,
				row.account_name,
				row.proposed_by_name,
				row.justification,
				reviewCaseRef(row.case_no),
				String(row.case_no ?? ''),
				reviewChangeTitle(row)
			)
	);
}

export function reviewSubject(row: Pick<ReviewRow, 'contact_name' | 'subject_type'>): string {
	return row.contact_name?.trim() || (row.subject_type === 'account' ? 'Account' : 'Client');
}

export function reviewAccount(row: Pick<ReviewRow, 'account_name' | 'account_uid'>): string | null {
	return row.account_name?.trim() || row.account_uid?.trim() || null;
}

// ---- the seats --------------------------------------------------------------------------

export const REVIEW_SEATS: readonly ReviewSeat[] = ['for_me', 'mine', 'waiting', 'settled'];

export const REVIEW_SEAT_LABELS: Record<ReviewSeat, string> = {
	for_me: 'For me',
	mine: 'Submitted by me',
	waiting: 'All waiting',
	settled: 'Settled'
};

/** The seat a URL names, or null for anything else (an older link, a typo). */
export function reviewSeatOf(value: string | null | undefined): ReviewSeat | null {
	return (REVIEW_SEATS as readonly string[]).includes(value ?? '') ? (value as ReviewSeat) : null;
}

/**
 * Whether a row belongs to a seat. "For me" is the pending cases the reader may decide; "mine"
 * every case they submitted, with its outcome once decided; "waiting" every pending case;
 * "settled" everything decided, withdrawn or conflicted.
 */
export function seatMatches(
	row: Pick<ReviewRow, 'status' | 'can_decide' | 'is_mine'>,
	seat: ReviewSeat
): boolean {
	switch (seat) {
		case 'for_me':
			return row.status === 'pending' && row.can_decide;
		case 'mine':
			// Everything the reader submitted, decided or not: "where is my request, and what
			// happened to it" is one question.
			return row.is_mine;
		case 'waiting':
			return row.status === 'pending';
		case 'settled':
			return row.status !== 'pending';
	}
}

/** How many rows each seat holds, for the switch's counts. Settled is not counted: the number
 *  is history, and a count beside "Settled" reads as work. */
export function reviewSeatCounts(
	rows: Pick<ReviewRow, 'status' | 'can_decide' | 'is_mine'>[]
): Record<ReviewSeat, number | null> {
	return {
		for_me: rows.filter((row) => seatMatches(row, 'for_me')).length,
		mine: rows.filter((row) => seatMatches(row, 'mine')).length,
		waiting: rows.filter((row) => seatMatches(row, 'waiting')).length,
		settled: null
	};
}

/** Where the queue opens with no seat in the URL: on the reader's own decisions when there are
 *  any, else on everything waiting — never on an empty "For me" while others wait. */
export function defaultReviewSeat(
	rows: Pick<ReviewRow, 'status' | 'can_decide' | 'is_mine'>[]
): ReviewSeat {
	return rows.some((row) => seatMatches(row, 'for_me')) ? 'for_me' : 'waiting';
}

// ---- how long it has waited -------------------------------------------------------------

export type WaitingTone = 'none' | 'warning' | 'danger';

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** `25m`, `9h`, `1d 9h`, `3d` — a duration as a queue says it, coarser as it grows. */
export function waitingLabel(ms: number): string {
	if (!Number.isFinite(ms) || ms < 0) return '—';
	const mins = Math.floor(ms / 60_000);
	if (mins < 1) return 'now';
	if (mins < 60) return `${mins}m`;
	const hours = Math.floor(ms / HOUR);
	if (hours < 24) return `${hours}h`;
	const days = Math.floor(ms / DAY);
	const rest = hours - days * 24;
	return days < 7 && rest ? `${days}d ${rest}h` : `${days}d`;
}

/** Plain under a day, amber past it, red past three: the same thresholds on both seats. */
export function waitingTone(ms: number): WaitingTone {
	if (!Number.isFinite(ms)) return 'none';
	if (ms >= 3 * DAY) return 'danger';
	if (ms >= DAY) return 'warning';
	return 'none';
}

/**
 * How long a case has been (or was) waiting: since it was submitted while it is pending, and
 * from submission to decision once settled. `now` is passed so SSR and the browser agree.
 */
export function reviewWaiting(
	row: Pick<ReviewRow, 'status' | 'proposed_at' | 'decided_at'>,
	now: number = Date.now()
): { ms: number; label: string; tone: WaitingTone; settled: boolean } {
	const from = new Date(row.proposed_at).getTime();
	const settled = row.status !== 'pending';
	const to = settled && row.decided_at ? new Date(row.decided_at).getTime() : now;
	const ms = Number.isNaN(from) || Number.isNaN(to) ? Number.NaN : Math.max(0, to - from);
	return {
		ms,
		label: waitingLabel(ms),
		tone: settled ? 'none' : waitingTone(ms),
		settled
	};
}

// ---- who decides ------------------------------------------------------------------------

/** Every role that may decide, by name. Older rows carry only the first. */
export function reviewApproverRoles(
	row: Pick<ReviewRow, 'approver_roles' | 'reviewer_role_name'>
): string[] {
	if (row.approver_roles?.length) return row.approver_roles;
	return row.reviewer_role_name ? [row.reviewer_role_name] : [];
}

export function reviewDecides(
	row: Pick<ReviewRow, 'approver_roles' | 'reviewer_role_name'>
): string {
	return reviewApproverRoles(row).join(' · ') || 'Any approver';
}

/** Nobody but the submitter holds an approver role: the case cannot be decided as things stand. */
export function reviewUndecidable(row: Pick<ReviewRow, 'status' | 'eligible_approvers'>): boolean {
	return row.status === 'pending' && row.eligible_approvers === 0;
}

/**
 * The one line under "Decides": whose move it is, said to this reader. "You hold Adviser" when
 * they may decide; "Nobody else holds Compliance" when no one can; else who it waits on and how
 * many of them there are.
 */
export function reviewDecidesSentence(
	row: Pick<
		ReviewRow,
		| 'status'
		| 'can_decide'
		| 'is_mine'
		| 'approver_roles'
		| 'reviewer_role_name'
		| 'eligible_approvers'
	>
): string {
	const roles = reviewDecides(row);
	if (row.status !== 'pending') return roles;
	if (row.can_decide) return `You hold ${roles}`;
	if (reviewUndecidable(row)) return `Nobody else holds ${roles}`;
	const n = row.eligible_approvers;
	if (typeof n === 'number' && n > 0)
		return `Waiting on ${roles} · ${n} ${n === 1 ? 'person' : 'people'}`;
	return `Waiting on ${roles}`;
}

// ---- the dialog copy --------------------------------------------------------------------

const MONEY_SEND_KEYS = new Set([
	'transaction.payment',
	'transaction.withdrawal',
	'transaction.fee',
	'transaction.transfer',
	'payment.regular',
	'withdrawal.regular',
	'contribution.lodge'
]);

/**
 * What approving DOES, said for the kind of hold it is — one sentence covered every kind, and
 * it was the book-review sentence even when approving meant sending £25,000 to a custodian.
 */
export function reviewApproveCopy(row: ReviewRow): { title: string; description: string } {
	const by = row.proposed_by_name ?? 'the person who submitted it';
	if (isActionReview(row)) {
		const money = inputMoney(row.input);
		if (MONEY_SEND_KEYS.has(row.action_key)) {
			const what = money != null ? `${formatGBP(money)} ` : '';
			return {
				title: `Approve and send ${what}now?`.replace('  ', ' '),
				description:
					'The instruction goes to the custodian as soon as you approve. Nothing on the book moves until the custodian accepts it; a refusal lands on Actions as needing attention.'
			};
		}
		return {
			title: 'Approve and run this now?',
			description:
				'The action runs at the provider as soon as you approve. A refusal lands on Actions as needing attention.'
		};
	}
	if (isBankReview(row)) {
		return {
			title: 'Approve this bank change?',
			description: `The new details write to the record as ${by}'s save. Future withdrawals will go to this account.`
		};
	}
	if (isVulnerabilityReview(row)) {
		return {
			title: 'Approve this vulnerability change?',
			description: `The vulnerability record changes as ${by}'s save, and the client's record shows it on every page.`
		};
	}
	if (isDetailsReview(row)) {
		const cards = reviewDetailCards(row).map((card) => DETAIL_CARD_LABELS[card].toLowerCase());
		const where = cards.length ? cards.join(' and ') : 'client details';
		return {
			title: 'Approve this change?',
			description: `The ${where} write to the record as ${by}'s save, and the record's own events fire then.`
		};
	}
	if (row.action_key === 'account.model_changed' || isFeePackagesReview(row)) {
		const what = isFeePackagesReview(row) ? 'fee packages' : 'model';
		return {
			title: `Approve this ${what} change?`,
			description: `The ${what} writes to the record as ${by}'s save, then goes to the custodian as its own run.`
		};
	}
	return {
		title: 'Approve this change?',
		description:
			'The change will apply now. Automations and notifications will fire as if the person who submitted it had just saved.'
	};
}

export function reviewRejectCopy(row: ReviewRow): { title: string; description: string } {
	if (isActionReview(row)) {
		return {
			title: 'Reject this request?',
			description:
				'Nothing is sent, and the booking behind it is cancelled. Say why — the person who raised it will see this.'
		};
	}
	return {
		title: 'Reject this change?',
		description: 'Nothing will apply. Say why — the person who submitted it will see this.'
	};
}

// ---- the next case ----------------------------------------------------------------------

/**
 * The case to open once `afterUid` is decided: the next one the reader may decide in the order
 * shown, wrapping to the first, or null when none is left. So a reviewer with eight to do opens
 * each from the last, rather than hunting for the next row.
 */
export function nextReviewFor(
	rows: Pick<ReviewRow, 'uid' | 'status' | 'can_decide'>[],
	afterUid: string
): string | null {
	const mine = rows.filter(
		(row) => row.uid !== afterUid && seatMatches({ ...row, is_mine: false }, 'for_me')
	);
	if (!mine.length) return null;
	const at = rows.findIndex((row) => row.uid === afterUid);
	const after = rows.slice(at + 1).find((row) => mine.includes(row));
	return (after ?? mine[0])?.uid ?? null;
}

// ---- the record's cards -----------------------------------------------------------------

/** The five cards of the client record a details edit can come from, and the contact columns
 *  each one saves — so a hold can be shown ON the card it will change. */
export type DetailCard = 'contact' | 'personal' | 'pension' | 'employer' | 'compliance';

export const DETAIL_CARD_LABELS: Record<DetailCard, string> = {
	contact: 'Contact details',
	personal: 'Personal details',
	pension: 'Pension details',
	employer: 'Employer details',
	compliance: 'Compliance'
};

export const DETAIL_CARD_FIELDS: Record<DetailCard, readonly string[]> = {
	contact: [
		'type',
		'service_type',
		'sub_type',
		'title',
		'first_name',
		'middle_name',
		'surname',
		'name',
		'email',
		'mobile',
		'phone',
		'address'
	],
	personal: [
		'dob',
		'gender',
		'occupation',
		'occupation_detail',
		'country_of_birth',
		'town_of_birth',
		'nationality',
		'national_identifier',
		'national_identifier_type',
		'tax_residency',
		'passport_number',
		'passport_country'
	],
	pension: [
		'marital_status',
		'employment_status',
		'retirement_age',
		'relevant_uk_earnings',
		'employer_contributions',
		'tax_relief',
		'flexi_access_taken',
		'flexi_access_date'
	],
	employer: [
		'employer_name',
		'employer_registration_number',
		'employer_address',
		'employer_bank_account'
	],
	compliance: [
		'us_person',
		'fatca_status',
		'fatca_certified_at',
		'source_of_funds',
		'source_of_funds_detail',
		'source_of_wealth',
		'source_of_wealth_detail'
	]
};

export function detailCardOf(key: string): DetailCard | null {
	for (const card of Object.keys(DETAIL_CARD_FIELDS) as DetailCard[]) {
		if (DETAIL_CARD_FIELDS[card].includes(key)) return card;
	}
	return null;
}

/** The cards a details hold will change, in the record's order. */
export function reviewDetailCards(row: Pick<ReviewRow, 'action_key' | 'payload'>): DetailCard[] {
	if (!isDetailsReview(row)) return [];
	const keys = Object.keys(asRecord(asRecord(row.payload)?.fields) ?? {});
	const cards = new Set(keys.map(detailCardOf).filter((card): card is DetailCard => !!card));
	return (Object.keys(DETAIL_CARD_FIELDS) as DetailCard[]).filter((card) => cards.has(card));
}

/** The pending details holds that touch one card. */
export function reviewsForCard(rows: ReviewRow[], card: DetailCard): ReviewRow[] {
	return rows.filter((row) => row.status === 'pending' && reviewDetailCards(row).includes(card));
}

/** The pending details holds that touch no known card — shown where the old strip was. */
export function reviewsOffTheCards(rows: ReviewRow[]): ReviewRow[] {
	return rows.filter(
		(row) => row.status === 'pending' && isDetailsReview(row) && reviewDetailCards(row).length === 0
	);
}

/** The labels of the fields a details edit actually moves. */
export function reviewChangedFields(
	row: Pick<ReviewRow, 'action_key' | 'payload' | 'before'>
): string[] {
	if (!isDetailsReview(row)) return [];
	return detailsChangedLabels(row);
}

/**
 * The second line of a queue row: what the case is about beyond its title — where it lands and
 * why it was raised, so the Account and Why columns can go.
 */
export function reviewChangeDetail(row: ReviewRow): string {
	const parts: string[] = [];
	if (isDetailsReview(row)) {
		const diff = reviewDetailsDiff(row);
		const moved = diff.filter((line) => line.changed).length;
		const cards = reviewDetailCards(row).map((card) => DETAIL_CARD_LABELS[card].toLowerCase());
		if (diff.length) {
			parts.push(
				`${moved || diff.length} of ${diff.length} field${diff.length === 1 ? '' : 's'}${cards.length ? ` on the ${cards.join(' and ')}` : ''}`
			);
		}
	} else if (isActionReview(row)) {
		const kind = typeof row.input?.kind === 'string' ? instructionKindLabel(row.input.kind) : null;
		if (kind) parts.push(kind);
		const account = reviewAccount(row);
		if (account) parts.push(account);
	} else {
		const account = reviewAccount(row);
		if (account) parts.push(account);
	}
	if (row.justification?.trim()) parts.push(`“${row.justification.trim()}”`);
	return parts.join(' · ');
}
