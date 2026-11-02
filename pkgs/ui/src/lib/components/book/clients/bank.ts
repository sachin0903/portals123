/**
 * A client's bank details — the one set of banking instructions per person
 * (`crm.contacts.bank_account`), used by every account they hold when the account has none
 * of its own (`platform.accounts.bank_accounts` wins; `app/bank_accounts.py`).
 *
 * Two rails, one object: UK (BACS — sort code + account number, plus which payment method: BACS,
 * the free default, or Faster Payment) or international (IBAN + SWIFT/BIC — always reaches SEI
 * over SWIFT, so there is no method to choose there). The API is the authority
 * (`app/contacts.py::normalise_bank_account`); this mirrors its rules so the adviser gets a
 * field-level message on the keystroke that caused it rather than a round-trip error, and so the
 * value that reaches the wire is already canonical.
 *
 * Nothing here logs. A validation message names the field, never the digits.
 *
 * Display grouping is the journey field masks (`forms/editor/masks.ts`) — a stored sort code is
 * `12-34-56`; the same `12 - 34 - 56` the adviser typed in a flow is what they read back here.
 */
import {
	canonicalSortCode,
	formatBankAccountNumber,
	formatSortCode
} from '../../../forms/editor/masks.js';
import type { BankDetails } from './types.js';

/** Which rail the form is collecting. Stored objects do not carry this — the fields are enough. */
export type BankScheme = 'bacs' | 'iban';

export const BANK_SCHEME_OPTIONS = [
	{ value: 'bacs', label: 'UK' },
	{ value: 'iban', label: 'International' }
] as const;

/** The UK rail only — an international row always reaches SEI over SWIFT, which having an IBAN
 *  already says, so there is nothing to choose there. BACS is free and the default; some firms
 *  pay by Faster Payment instead (`docs/api.md` → withdrawals). */
export type BankMethod = 'bacs' | 'faster_payment';

export const BANK_METHOD_OPTIONS = [
	{ value: 'bacs', label: 'BACS' },
	{ value: 'faster_payment', label: 'Faster Payment' }
] as const;

/** What the form collects, before normalisation. `method` only matters on the UK rail; a form on
 *  the international rail may omit it. */
export type BankInput = {
	scheme: BankScheme;
	name: string;
	bank?: string;
	sort_code: string;
	account_number: string;
	iban: string;
	bic: string;
	method?: BankMethod;
};

export type BankValidation =
	{ ok: true; value: BankDetails | null } | { ok: false; field: keyof BankInput; error: string };

/** PUT body that clears every key, so "cleared" cannot be read as "unchanged". */
export const CLEARED_BANK = {
	name: null,
	bank: null,
	sort_code: null,
	account_number: null,
	iban: null,
	bic: null,
	method: null
} as const;

const digitsOf = (s: string): string => s.replace(/[^0-9]/g, '');

const compactAlpha = (s: string): string => s.replace(/[\s-]/g, '').toUpperCase();

/** A person's name: letters (Unicode-aware, so François/Müller pass), spaces, hyphens or
 *  apostrophes only — no digits or other symbols. Mirrors `app/contacts.py::_is_person_name`. */
const NAME_CHARS = /^[\p{L} '-]+$/u;
const NAME_CHARS_ERROR = 'Use letters, spaces, hyphens or apostrophes only.';

/** Compact IBAN grouped in fours, which is how one is read aloud. */
export function formatIban(raw: string): string {
	const compact = compactAlpha(raw);
	return compact.replace(/(.{4})/g, '$1 ').trim();
}

function ibanIsValid(iban: string): boolean {
	if (!/^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$/.test(iban)) return false;
	const rearranged = iban.slice(4) + iban.slice(0, 4);
	const numeric = rearranged.replace(/[A-Z]/g, (ch) => String(ch.charCodeAt(0) - 55));
	return BigInt(numeric) % 97n === 1n;
}

function bicIsValid(bic: string): boolean {
	return /^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$/.test(bic);
}

/** Infer the rail from stored fields. An IBAN row has no sort code. */
export function bankSchemeOf(b: BankDetails | null | undefined): BankScheme {
	if (b?.iban && !b.sort_code) return 'iban';
	return 'bacs';
}

/** BACS is the default and is not written (the same convention `bank` follows) — so an absent
 *  or unrecognised value reads as BACS, never as a blank. */
export function bankMethodLabel(method: string | null | undefined): string {
	const labels: Record<string, string> = {
		bacs: 'BACS',
		chaps: 'CHAPS',
		faster_payment: 'Faster Payment',
		direct_debit: 'Direct Debit',
		cheque: 'Cheque',
		swift: 'SWIFT',
		transfer: 'Bank transfer'
	};
	return method ? (labels[method.toLowerCase()] ?? method) : 'BACS';
}

/** Register rows for the active rail. A leftover `bank` on a BACS row is shown; it is not asked.
 *
 *  Payment method is stated on the UK rail even at its default — the client and account bank
 *  cards are where that choice is made. The withdrawal drawer omits the row: the destination
 *  is the account, not the rail. */
export function bankDisplayRows(b: BankDetails): [string, string | null][] {
	const name: [string, string | null] = ['Account name', b.name ?? null];
	if (bankSchemeOf(b) === 'iban') {
		return [name, ['IBAN', b.iban ? formatIban(b.iban) : null], ['SWIFT/BIC', b.bic ?? null]];
	}
	return [
		...(b.bank ? ([['Bank', b.bank]] as [string, string | null][]) : []),
		name,
		['Sort code', b.sort_code ? formatSortCode(b.sort_code) : null],
		['Account number', b.account_number ? formatBankAccountNumber(b.account_number) : null],
		['Payment method', bankMethodLabel(b.method)]
	];
}

/**
 * Validate + canonicalise the bank form. An entirely blank form CLEARS the details (`value: null`)
 * rather than storing an empty object — blanking the fields is how an adviser removes a stale
 * mandate. `scheme` chooses which rail is required, so switching to International and leaving IBAN
 * blank names IBAN rather than complaining about a sort code the adviser is not looking at.
 *
 * A 7-digit account number is left-padded to 8, which is the Bacs convention: a bank that quotes
 * seven digits means a leading zero.
 */
export function validateBank(input: BankInput): BankValidation {
	const scheme: BankScheme = input.scheme === 'iban' ? 'iban' : 'bacs';
	const trimmed = {
		name: input.name.trim(),
		bank: (input.bank ?? '').trim(),
		sort_code: input.sort_code.trim(),
		account_number: input.account_number.trim(),
		iban: input.iban.trim(),
		bic: input.bic.trim()
	};

	if (scheme === 'iban') {
		if (!trimmed.name && !trimmed.iban && !trimmed.bic) return { ok: true, value: null };
		const iban = compactAlpha(trimmed.iban);
		if (!iban) return { ok: false, field: 'iban', error: 'Enter an IBAN.' };
		if (!ibanIsValid(iban)) return { ok: false, field: 'iban', error: 'Enter a valid IBAN.' };
		const bic = compactAlpha(trimmed.bic);
		if (!bic) return { ok: false, field: 'bic', error: 'Enter a SWIFT/BIC.' };
		if (!bicIsValid(bic))
			return { ok: false, field: 'bic', error: 'Enter an 8 or 11-character SWIFT/BIC.' };
		if (!trimmed.name)
			return { ok: false, field: 'name', error: 'Enter the account holder’s name.' };
		if (!NAME_CHARS.test(trimmed.name))
			return { ok: false, field: 'name', error: NAME_CHARS_ERROR };
		return { ok: true, value: { name: trimmed.name, iban, bic } };
	}

	if (!trimmed.name && !trimmed.bank && !trimmed.sort_code && !trimmed.account_number)
		return { ok: true, value: null };

	const sort = digitsOf(trimmed.sort_code);
	if (sort.length !== 6)
		return { ok: false, field: 'sort_code', error: 'Enter a 6-digit sort code.' };
	const account = digitsOf(trimmed.account_number);
	if (account.length !== 7 && account.length !== 8)
		return { ok: false, field: 'account_number', error: 'Enter an 8-digit account number.' };
	if (!trimmed.name) return { ok: false, field: 'name', error: 'Enter the account holder’s name.' };
	if (!NAME_CHARS.test(trimmed.name)) return { ok: false, field: 'name', error: NAME_CHARS_ERROR };

	return {
		ok: true,
		value: {
			name: trimmed.name,
			sort_code: canonicalSortCode(sort),
			account_number: account.padStart(8, '0'),
			...(trimmed.bank ? { bank: trimmed.bank } : {}),
			// BACS is the default and is not written — only the non-default choice is, so
			// reverting to BACS clears a previously-stored Faster Payment rather than leaving it
			// stranded (there is no PASSTHROUGH of a stale `method` to leak it back in).
			...(input.method === 'faster_payment' ? { method: 'faster_payment' as const } : {})
		}
	};
}

export type BankDirectoryStatus = 'idle' | 'checking' | 'confirmed' | 'rejected' | 'unavailable';

const DIRECTORY_CHECKING = 'Checking these bank details…';
const DIRECTORY_ACCOUNT = "This account number isn't valid for that sort code.";

/**
 * Why a UK save cannot go through yet, from the live sort-code check.
 *
 * An international rail, a confirmed pair, a pair that is not complete, and a directory that
 * cannot be reached all return null — the last of those is allowed to save. A rejection names
 * the sort code when the directory does not know it, and the account number otherwise.
 */
export function bankDirectoryError(
	scheme: BankScheme,
	status: BankDirectoryStatus,
	state: string | null | undefined,
	message: string | null | undefined
): { field: 'sort_code' | 'account_number'; error: string } | null {
	if (scheme === 'iban' || status === 'confirmed' || status === 'idle' || status === 'unavailable')
		return null;
	if (status === 'checking') return { field: 'account_number', error: DIRECTORY_CHECKING };
	return {
		field: state === 'sort_code_unknown' ? 'sort_code' : 'account_number',
		error: message?.trim() || DIRECTORY_ACCOUNT
	};
}

/**
 * Read the bank details off a contact record.
 *
 * `GET /crm/contacts/{id}` carries `bank_account` as free-form jsonb, and rows written before it was
 * validated may hold anything — so every field is narrowed rather than trusted, and a row with no
 * usable detail reads as absent instead of rendering "null" at an adviser.
 */
export function readBankDetails(contact: unknown): BankDetails | null {
	if (typeof contact !== 'object' || contact === null) return null;
	const raw = (contact as { bank_account?: unknown }).bank_account;
	if (typeof raw !== 'object' || raw === null) return null;
	const pick = (k: string): string | null => {
		const v = (raw as Record<string, unknown>)[k];
		return typeof v === 'string' && v.trim() ? v.trim() : typeof v === 'number' ? String(v) : null;
	};
	const method = pick('method');
	const value: BankDetails = {
		name: pick('name'),
		bank: pick('bank'),
		sort_code: pick('sort_code'),
		account_number: pick('account_number'),
		iban: pick('iban'),
		bic: pick('bic'),
		// Account-level provider rows carry a wider, server-validated vocabulary than the client
		// editor writes. Keep its label visible rather than misreporting every wider rail as BACS.
		method
	};
	return Object.values(value).some(Boolean) ? value : null;
}
