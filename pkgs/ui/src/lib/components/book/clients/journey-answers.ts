/**
 * A completed journey's forms, as a person reads them on the client record.
 *
 * The engine stores field keys and nested values. The form definition has the label, the
 * section and the type, so this is the one place that turns a response into sections a
 * reader can scan — including a repeating schedule as rows, not as JSON.
 */
import { formatFieldValue } from '../../../forms/editor/format.js';
import type { FormField } from '../../../forms/types.js';
import { repeatingColumns } from '../../../forms/repeating.js';
import { humanise } from '../../../sentence-case.js';

export type JourneyField = {
	key: string;
	label: string;
	section: string | null;
	field_type: string;
	options: unknown;
};

export type PresentedMoney = { amount: number; currency: string };

export type PresentedCell = { text: string; money?: PresentedMoney };

export type PresentedTable = { columns: string[]; rows: string[][]; cells: PresentedCell[][] };

/** A money answer carries its figure so the reader can split pounds from pence; `value` stays text. */
export type PresentedRow = {
	label: string;
	value: string;
	table?: PresentedTable;
	money?: PresentedMoney;
};

export type PresentedSection = { heading: string | null; rows: PresentedRow[] };

export type ScoredResult = { headline: string; definition: string | null };

const SNAKE = /^[a-z]+(_[a-z0-9]+)*$/;

/** A stored country code (`GB`) reads as the country. Applied only where the field is a place. */
const REGION_FIELD = /nationalit|tax_residen|country|region/i;

const ADDRESS_KEYS = [
	'line1',
	'line_1',
	'address_line_1',
	'line2',
	'line_2',
	'address_line_2',
	'city',
	'town',
	'county',
	'postcode',
	'postal_code',
	'country'
];

function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function blank(value: unknown): boolean {
	if (value == null || value === '') return true;
	if (Array.isArray(value)) return value.length === 0;
	if (isRecord(value)) return Object.keys(value).length === 0;
	return false;
}

/** A scored questionnaire's band, read from the response's outputs rather than its answers. */
export function scoredResult(
	outputs: Record<string, unknown> | null | undefined
): ScoredResult | null {
	if (!outputs) return null;
	const label =
		typeof outputs.label === 'string' && outputs.label.trim()
			? outputs.label.trim()
			: typeof outputs.band === 'string' && outputs.band.trim()
				? humanise(outputs.band.trim())
				: '';
	const score = outputs.score;
	const hasScore = typeof score === 'number' || (typeof score === 'string' && score !== '');
	if (!label && !hasScore) return null;
	const headline = label && hasScore ? `${label} · ${score}` : label || String(score);
	const definition =
		typeof outputs.definition === 'string' && outputs.definition.trim()
			? outputs.definition.trim()
			: null;
	return { headline, definition };
}

function asField(
	field: Pick<JourneyField, 'field_type' | 'options'>
): Pick<FormField, 'fieldType' | 'options'> {
	const type =
		field.field_type === 'segmented' || field.field_type === 'radio' ? 'select' : field.field_type;
	return { fieldType: type, options: field.options as FormField['options'] };
}

function readable(value: string): string {
	return SNAKE.test(value) ? humanise(value) : value;
}

function regionName(code: string): string | null {
	if (!/^[A-Za-z]{2}$/.test(code)) return null;
	try {
		const name = new Intl.DisplayNames('en-GB', { type: 'region' }).of(code.toUpperCase());
		return name && name.toUpperCase() !== code.toUpperCase() ? name : null;
	} catch {
		return null;
	}
}

function place(value: string, field: { key?: string; field_type: string }): string {
	if (!REGION_FIELD.test(`${field.field_type} ${field.key ?? ''}`)) return value;
	return value
		.split(', ')
		.map((part) => regionName(part.trim()) ?? part)
		.join(', ');
}

function formatScalar(
	value: unknown,
	field: { key?: string; field_type: string; options: unknown }
): string {
	if (isRecord(value)) return formatObject(value);
	const formatted = formatFieldValue({ value, field: asField(field) });
	const text = formatted === String(value ?? '') ? readable(formatted) : formatted;
	return place(text, field);
}

function formatObject(value: Record<string, unknown>): string {
	const address = ADDRESS_KEYS.map((key) => {
		const part = value[key];
		if (typeof part !== 'string' || !part.trim()) return '';
		const text = part.trim();
		return key === 'country' ? (regionName(text) ?? text) : text;
	}).filter((part) => part !== '');
	if (address.length) return address.join(', ');
	return Object.entries(value)
		.filter(([, part]) => !blank(part))
		.map(([key, part]) => `${humanise(key)}: ${formatLoose(part)}`)
		.join(', ');
}

function formatLoose(value: unknown): string {
	if (typeof value === 'boolean') return value ? 'Yes' : 'No';
	if (value == null) return '';
	const text = String(value);
	return readable(text);
}

/** The figure behind a currency/money answer, parsed the way `formatFieldValue` does. */
function moneyOf(
	value: unknown,
	field: { field_type: string; options: unknown }
): PresentedMoney | undefined {
	if (field.field_type !== 'currency' && field.field_type !== 'money') return undefined;
	if (isRecord(value) || Array.isArray(value)) return undefined;
	const amount = typeof value === 'number' ? value : Number(String(value).replace(/,/g, ''));
	if (!Number.isFinite(amount)) return undefined;
	const raw = isRecord(field.options) ? field.options.currency : null;
	return { amount, currency: typeof raw === 'string' && raw ? raw : 'GBP' };
}

function repeatingTable(value: unknown[], field: JourneyField): PresentedTable | null {
	const columns = repeatingColumns(field.options as FormField['options']);
	const records = value.filter(isRecord);
	if (!columns.length || !records.length) return null;
	const cells = records.map((row) =>
		columns.map((column): PresentedCell => {
			const cell = row[column.key];
			if (blank(cell)) return { text: '—' };
			const field = { field_type: column.type, options: column.options ?? null };
			return { text: formatScalar(cell, field), money: moneyOf(cell, field) };
		})
	);
	return {
		columns: columns.map((column) => column.label),
		rows: cells.map((row) => row.map((cell) => cell.text)),
		cells
	};
}

function rowFor(field: JourneyField, value: unknown): PresentedRow | null {
	if (blank(value)) return null;
	if (field.field_type === 'repeating' && Array.isArray(value)) {
		const table = repeatingTable(value, field);
		if (table) return { label: field.label, value: '', table };
	}
	const formatted = formatScalar(value, field);
	if (!formatted) return null;
	const money = moneyOf(value, field);
	return money
		? { label: field.label, value: formatted, money }
		: { label: field.label, value: formatted };
}

/**
 * Answered fields, in the form's own order, grouped by the section they were authored under.
 * A key the definition no longer has is still shown — an answer is not dropped because the
 * form moved on.
 */
export function presentAnswers(
	fields: JourneyField[],
	answers: Record<string, unknown>
): PresentedSection[] {
	const seen = new Set<string>();
	const sections: PresentedSection[] = [];
	const push = (heading: string | null, row: PresentedRow) => {
		const last = sections.at(-1);
		if (last && last.heading === heading) last.rows.push(row);
		else sections.push({ heading, rows: [row] });
	};

	for (const field of fields) {
		if (!field.key) continue;
		seen.add(field.key);
		const row = rowFor(field, answers[field.key]);
		if (row) push(field.section, row);
	}

	for (const [key, value] of Object.entries(answers)) {
		if (seen.has(key) || blank(value)) continue;
		const row = rowFor(
			{ key, label: humanise(key), section: null, field_type: 'text', options: null },
			value
		);
		if (row) push(null, row);
	}
	return sections;
}
