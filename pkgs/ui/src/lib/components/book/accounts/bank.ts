/**
 * An account's own bank details (`platform.accounts.bank_accounts`), with the client's as the
 * fallback.
 *
 * Resolution is account first, client second (`app/bank_accounts.py`). SEI holds pay-to
 * profiles per account; Seccl holds them per client. `[0]` is the default a payment takes, and
 * this surface edits that first entry — a picker appears only when there is more than one,
 * and is not built yet. Saving a single object therefore replaces `[0]` and keeps the rest;
 * clearing the form clears the whole list so the account falls back to the client again.
 *
 * The shape of one entry is `contacts.normalise_bank_account`'s (the same object the client
 * card writes) plus `method` — the UK payment rail, BACS or Faster Payment, which this editor
 * DOES collect (`validateBank`'s output carries it) — and vendor passthrough this editor does not
 * collect and must not wipe.
 */
import { readBankDetails } from '../clients/bank.js';
import type { BankDetails } from '../clients/types.js';

/** Keys this editor does not collect. Copied through from the stored `[0]` so a name change
 *  does not drop the custodian's own profile id. IBAN is not here: it is a core rail, and
 *  `method` is not here either — it is a first-class field the editor collects itself
 *  (`validateBank`'s output), so a save that omits it (BACS, the default) must CLEAR a
 *  previously-stored Faster Payment rather than have this passthrough leak the stale value back
 *  in. */
const PASSTHROUGH = [
	'direction',
	'reference',
	'mandate_accepted_on',
	'provider',
	'provider_ref',
	'verification_status'
] as const;

/** The stored array as objects, whatever jsonb arrived as. Empty / missing → none of its own. */
export function ownBankAccounts(value: unknown): Record<string, unknown>[] {
	if (!Array.isArray(value)) return [];
	return value.filter(
		(item): item is Record<string, unknown> =>
			!!item && typeof item === 'object' && !Array.isArray(item)
	);
}

/** True when GET sent no account-level entries — the card then shows the client's and says so. */
export function usesClientBank(account: { bank_accounts?: unknown }): boolean {
	return ownBankAccounts(account.bank_accounts).length === 0;
}

/** What the card displays: the account's `[0]`, else the client's. */
export function resolvedBank(account: {
	bank_accounts?: unknown;
	client: { bank_account?: unknown };
}): BankDetails | null {
	const own = ownBankAccounts(account.bank_accounts)[0];
	if (own) return readBankDetails({ bank_account: own });
	return readBankDetails({ bank_account: account.client.bank_account });
}

/**
 * PUT `/crm/accounts/{uid}/bank-accounts` body. A LIST, always — the API refuses a bare object
 * (`normalise_entries`), because `[0]` is the default a payment takes.
 *
 * `value` null (the form cleared) → `{ bank_accounts: null }`. The single-entry editor cannot
 * name which of several profiles to drop, so clearing falls back to the client for all of them.
 */
export function accountBankSaveBody(
	value: BankDetails | null,
	existing: unknown
): { bank_accounts: Record<string, unknown>[] | null } {
	if (value == null) return { bank_accounts: null };
	const list = ownBankAccounts(existing);
	const kept: Record<string, unknown> = {};
	const first = list[0];
	if (first) {
		for (const key of PASSTHROUGH) {
			const raw = first[key];
			if (raw != null && String(raw).trim()) kept[key] = raw;
		}
		// This editor owns BACS/Faster Payment, so omitting Faster Payment means "revert to BACS".
		// Wider provider-authored rails (CHAPS, SWIFT, Direct Debit…) are not choices this form can
		// safely reinterpret; preserve one on an otherwise unrelated edit.
		const method = typeof first.method === 'string' ? first.method.trim().toLowerCase() : '';
		if (method && method !== 'bacs' && method !== 'faster_payment') kept.method = first.method;
	}
	return { bank_accounts: [{ ...kept, ...value }, ...list.slice(1)] };
}
