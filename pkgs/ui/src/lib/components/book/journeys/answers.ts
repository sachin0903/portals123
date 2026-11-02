/**
 * Submitted fill_form answers as a person reads them on the journey.
 *
 * The engine stores field keys (`dob`, `bank_account.sort_code`) and nested values (an address
 * object). `String(value)` turns that address into `[object Object]` and leaves the keys as
 * snake_case — which is what the expanded step used to dump. This module is the one place that
 * flattens, labels, and formats so the page and the tests cannot drift.
 */
import { formatBankAccountNumber, formatSortCode } from '../../../forms/editor/masks.js';
import { formatDate } from '../../../dates.js';

export type AnswerRow = { key: string; label: string; value: string };

export type AnswerSection = { heading: string | null; rows: AnswerRow[] };

const FIELD_LABELS: Record<string, string> = {
	dob: 'Date of birth',
	model_id: 'Investment model',
	fee_package_ids: 'Fee packages',
	dividend_settlement_currency: 'Preferred settlement currency',
	sort_code: 'Sort code',
	account_name: 'Account name',
	account_number: 'Account number',
	national_identifier_type: 'Identifier type',
	// `phone` stays the fact-find's mobile so an in-flight answer still binds. The other number
	// is `other_numbers`, shown as Phone.
	phone: 'Mobile number',
	other_numbers: 'Phone'
};

/** Reading order for a person — same sequence as the onboarding seed's core fact-find. */
const KEY_ORDER = [
	'title',
	'first_name',
	'middle_name',
	'surname',
	'last_name',
	'dob',
	'gender',
	'nationality',
	'tax_residency',
	'national_identifier',
	'national_identifier_type',
	'email',
	'phone',
	'mobile',
	'mobile_phone',
	'other_numbers',
	'phone_numbers',
	'address',
	'bank_account.account_name',
	'bank_account.account_number',
	'bank_account.sort_code'
];

const VALUE_LABELS: Record<string, string> = {
	ni_number: 'National Insurance number',
	national_insurance: 'National Insurance',
	passport: 'Passport'
};

const REGION_KEYS = /(^|\.)(nationality|tax_residency|country|country_code)$/i;

/** A stored key as a person would say it: `bank_account.sort_code` → "Bank account · Sort code". */
export function answerLabel(key: string): string {
	return key
		.split('.')
		.map((part) => FIELD_LABELS[part] ?? humanise(part))
		.join(' · ');
}

function row(key: string, value: string): AnswerRow {
	const k = key || 'value';
	return { key: k, label: answerLabel(k), value };
}

/** Flatten nested answers into labelled rows, formatting objects and wire values on the way. */
export function answerRows(
	answers: Record<string, unknown>,
	displayValues: Record<string, unknown> = {}
): AnswerRow[] {
	return flatten({ ...answers, ...displayValues }, '');
}

/**
 * Answers in reading order, with dotted keys (bank_account.*) grouped under a heading so the
 * page can draw one aligned register instead of a two-column dump that splits related fields.
 */
export function answerSections(
	answers: Record<string, unknown>,
	displayValues: Record<string, unknown> = {}
): AnswerSection[] {
	const ranked = answerRows(answers, displayValues)
		.map((item, index) => ({ ...item, index }))
		.sort((a, b) => rank(a.key) - rank(b.key) || a.index - b.index);

	const sections: AnswerSection[] = [];
	for (const item of ranked) {
		const prefix = item.key.includes('.') ? item.key.split('.')[0] : null;
		const heading = prefix ? answerLabel(prefix) : null;
		const label = prefix ? answerLabel(item.key.split('.').pop() ?? item.key) : item.label;
		const last = sections.at(-1);
		if (heading && last?.heading === heading) {
			last.rows.push({ key: item.key, label, value: item.value });
		} else if (!heading && last && last.heading === null) {
			last.rows.push({ key: item.key, label, value: item.value });
		} else {
			sections.push({ heading, rows: [{ key: item.key, label, value: item.value }] });
		}
	}
	return sections;
}

function rank(key: string): number {
	const exact = KEY_ORDER.indexOf(key);
	if (exact >= 0) return exact;
	const leaf = KEY_ORDER.indexOf(key.split('.').pop() ?? key);
	return leaf >= 0 ? leaf : KEY_ORDER.length;
}

function flatten(value: unknown, key: string): AnswerRow[] {
	if (value == null || value === '') return [];
	if (Array.isArray(value)) {
		if (value.every((item) => item == null || typeof item !== 'object')) {
			const joined = value
				.map((item) => formatScalar(item, key))
				.filter((item) => item !== '')
				.join(', ');
			return joined ? [row(key, joined)] : [];
		}
		if (isPhoneNumberList(value)) {
			const line = formatPhoneNumberList(value);
			return line ? [row(key || 'phone_numbers', line)] : [];
		}
		return value.flatMap((item, i) => flatten(item, key ? `${key}.${i + 1}` : String(i + 1)));
	}
	if (typeof value === 'object') {
		const record = value as Record<string, unknown>;
		if (isAddress(record)) {
			const line = formatAddressLike(record);
			return line ? [row(key || 'address', line)] : [];
		}
		return Object.entries(record).flatMap(([k, v]) => flatten(v, key ? `${key}.${k}` : k));
	}
	const formatted = formatScalar(value, key);
	return formatted ? [row(key, formatted)] : [];
}

function formatScalar(value: unknown, key: string): string {
	if (value == null || value === '') return '';
	if (typeof value === 'boolean') return value ? 'Yes' : 'No';
	if (typeof value === 'number') return String(value);
	if (typeof value !== 'string') return String(value);

	const date = formatIsoDate(value);
	if (date) return date;

	if (REGION_KEYS.test(key)) {
		const region = regionName(value);
		if (region) return region;
	}

	const leaf = key.split('.').pop() ?? key;
	if (leaf === 'sort_code') return formatSortCode(value) || value;
	if (leaf === 'account_number') return formatBankAccountNumber(value) || value;

	if (VALUE_LABELS[value]) return VALUE_LABELS[value];
	if (/^[a-z]+(_[a-z0-9]+)*$/.test(value)) return humanise(value);
	return value;
}

/**
 * The `phoneNumbers` field's answer: an array of `{ label, number }`.
 *
 * Flattened the generic way it becomes four rows — "Phone · 1 · Label", "Phone · 1
 * · Number" and so on — which is a register of the SHAPE rather than of the answer, and reads worse
 * than the `[object Object]` this module exists to stop. It is ONE fact ("the other numbers they
 * gave us"), and its order is the applicant's own, so it renders as one line in that order.
 */
function isPhoneNumberList(value: unknown[]): value is { label?: unknown; number?: unknown }[] {
	return (
		value.length > 0 &&
		value.every(
			(item) => item != null && typeof item === 'object' && !Array.isArray(item) && 'number' in item
		)
	);
}

function formatPhoneNumberList(value: { label?: unknown; number?: unknown }[]): string {
	return value
		.map((item) => {
			const number = String(item.number ?? '').trim();
			if (!number) return '';
			const name = String(item.label ?? '').trim();
			return name ? `${humanise(name)} ${number}` : number;
		})
		.filter((part) => part !== '')
		.join(', ');
}

function isAddress(value: Record<string, unknown>): boolean {
	return ['line1', 'address_line1', 'postcode', 'post_code'].some((k) => k in value);
}

function formatAddressLike(value: Record<string, unknown>): string {
	return [
		value.line1 ?? value.address_line1,
		value.line2 ?? value.address_line2,
		value.line3 ?? value.address_line3,
		value.city ?? value.town ?? value.posttown,
		value.region ?? value.county,
		value.postcode ?? value.post_code,
		typeof value.country === 'string' ? (regionName(value.country) ?? value.country) : value.country
	]
		.filter((part): part is string => typeof part === 'string' && part.trim() !== '')
		.join(', ');
}

/** Date-only ISO (`2026-09-09`) in en-GB, parsed as UTC so the day does not shift with the host tz. */
function formatIsoDate(value: string): string | null {
	const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
	if (!match) return null;
	const day = Number(match[3]);
	const month = Number(match[2]);
	const year = Number(match[1]);
	const date = new Date(Date.UTC(year, month - 1, day));
	if (
		date.getUTCFullYear() !== year ||
		date.getUTCMonth() !== month - 1 ||
		date.getUTCDate() !== day
	) {
		return null;
	}
	// `formatDate` reads a bare `YYYY-MM-DD` as UTC.
	return formatDate(value, '') || null;
}

function regionName(code: string): string | null {
	if (!/^[A-Za-z]{2}$/.test(code)) return null;
	try {
		const name = new Intl.DisplayNames('en-GB', { type: 'region' }).of(code.toUpperCase());
		return name && name !== code.toUpperCase() ? name : null;
	} catch {
		return null;
	}
}

function humanise(value: string): string {
	const spaced = value.replace(/_/g, ' ');
	return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
