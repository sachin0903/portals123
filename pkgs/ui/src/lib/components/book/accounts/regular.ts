/**
 * Regular payments and withdrawals on an account (`platform.accounts.payments` /
 * `withdrawals` jsonb).
 *
 * Two dialects exist: the register this surface renders (`RegularMovement[]`) and the object
 * onboarding wrote (`regular_payments: {amount, frequency, date}`, or a bare withdrawal
 * object). Nothing else writes these columns (`docs/api-integrations.md` §4.3), so a book that
 * has never been through onboarding shows an empty card. `asRegularMovements` is the one
 * reader, so a shape the form did not author still displays rather than reading as "none set up".
 */
import { sentenceCase } from '../../../sentence-case.js';
import type { RegularMovement } from './types.js';

export const MOVEMENT_FREQUENCIES = [
	{ value: 'monthly', label: 'Monthly' },
	{ value: 'quarterly', label: 'Quarterly' },
	{ value: 'half_yearly', label: 'Half-yearly' },
	{ value: 'annually', label: 'Yearly' }
] as const;

/** What the add-payment / add-withdrawal drawer offers. Half-yearly stays readable on rows
 *  already stored; it is not a choice on a new schedule. */
export const SCHEDULE_FREQUENCIES = [
	{ value: 'monthly', label: 'Monthly' },
	{ value: 'quarterly', label: 'Quarterly' },
	{ value: 'annually', label: 'Yearly' }
] as const;

const FREQUENCY_LABELS: Record<string, string> = {
	monthly: 'Monthly',
	quarterly: 'Quarterly',
	half_yearly: 'Half-yearly',
	annually: 'Yearly',
	yearly: 'Yearly'
};

export function movementFrequencyLabel(value: string | null | undefined): string {
	if (!value) return '—';
	return FREQUENCY_LABELS[value] ?? sentenceCase(value);
}

/** The choices on an update. A stored half-yearly (or anything else already on the row) stays
 *  selectable, so opening the dialog does not silently retarget the instruction. */
export function scheduleFrequencyChoices(
	current: string | null | undefined
): { value: string; label: string }[] {
	const choices: { value: string; label: string }[] = SCHEDULE_FREQUENCIES.map((item) => ({
		value: item.value,
		label: item.label
	}));
	if (current && !choices.some((item) => item.value === current)) {
		const label = movementFrequencyLabel(current);
		if (label !== '—') choices.push({ value: current, label });
	}
	return choices;
}

/**
 * The clause beside the amount on the account rail: how often, and when it next runs.
 *
 * A missing method used to be printed with `humanise`, which is an em dash, so the row read
 * "— · next 1 Oct 2026". A dash is not a payment method, and the rail is too narrow for a
 * second line of it. Nothing here stands in for a fact the row does not have.
 */
export function movementScheduleCaption(
	frequency: string | null | undefined,
	nextDate: string | null | undefined,
	formatDate: (iso: string) => string
): string | null {
	const named = frequency ? movementFrequencyLabel(frequency) : null;
	const when = nextDate?.trim() ? formatDate(nextDate) : null;
	if (named && named !== '—' && when) return `${named} · next ${when}`;
	if (when) return `Next ${when}`;
	if (named && named !== '—') return named;
	return null;
}

export const MOVEMENT_METHODS = [
	{ value: 'direct_debit', label: 'Direct debit' },
	{ value: 'bank_transfer', label: 'Bank transfer' },
	{ value: 'cheque', label: 'Cheque' },
	{ value: 'internal', label: 'Internal' },
	{ value: 'card', label: 'Card' }
] as const;

export type MovementDraft = {
	amount: number | null;
	frequency: string;
	method: string;
	next_date: string;
	reference: string;
};

const FREQUENCY_VALUES = new Set<string>(MOVEMENT_FREQUENCIES.map((item) => item.value));

function asString(value: unknown): string | null {
	if (typeof value !== 'string') return null;
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

function asAmount(value: unknown): number | null {
	if (typeof value === 'number') return Number.isFinite(value) ? value : null;
	if (typeof value === 'string' && value.trim()) {
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : null;
	}
	return null;
}

function asMovement(value: unknown): RegularMovement | null {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
	const rec = value as Record<string, unknown>;
	const amount = asAmount(rec.amount);
	const frequency = asString(rec.frequency);
	const nextDate = asString(rec.next_date) ?? asString(rec.date);
	const method = asString(rec.method);
	const reference = asString(rec.reference);
	if (amount == null && !frequency && !nextDate && !method && !reference) return null;
	return {
		amount,
		frequency,
		next_date: nextDate,
		method,
		reference
	};
}

/** The register this card renders, whatever dialect the jsonb arrived in. */
export function asRegularMovements(value: unknown): RegularMovement[] {
	if (Array.isArray(value)) {
		return value.map(asMovement).filter((row): row is RegularMovement => row != null);
	}
	if (!value || typeof value !== 'object') return [];
	const rec = value as Record<string, unknown>;
	if ('regular_payments' in rec) return asRegularMovements(rec.regular_payments);
	const one = asMovement(rec);
	return one ? [one] : [];
}

export function emptyMovementDraft(): MovementDraft {
	return { amount: null, frequency: 'monthly', method: '', next_date: '', reference: '' };
}

export function draftsFrom(rows: RegularMovement[]): MovementDraft[] {
	if (!rows.length) return [emptyMovementDraft()];
	return rows.map((row) => ({
		amount: row.amount ?? null,
		frequency: row.frequency && FREQUENCY_VALUES.has(row.frequency) ? row.frequency : 'monthly',
		method: row.method ?? '',
		next_date: (row.next_date ?? '').slice(0, 10),
		reference: row.reference ?? ''
	}));
}

/** Rows the form will save — empty amounts drop out, so clearing the last row is a clear. */
export function movementsFromDrafts(rows: MovementDraft[]): RegularMovement[] {
	return rows.flatMap((row) => {
		if (row.amount == null || !Number.isFinite(row.amount) || row.amount <= 0) return [];
		const frequency = FREQUENCY_VALUES.has(row.frequency) ? row.frequency : null;
		return [
			{
				amount: row.amount,
				frequency,
				next_date: row.next_date.trim() || null,
				method: row.method.trim() || null,
				reference: row.reference.trim() || null
			}
		];
	});
}
