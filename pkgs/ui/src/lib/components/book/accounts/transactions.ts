/** Transaction shapes for the shared book listings. Mirrors a `GET /crm/transactions` row —
 *  `platform.transactions` with the account, client and adviser joined on — which is what every
 *  surface rendering these now reads. Moved from apps/platform `$lib/transactions/types`. */

export type TransactionStatus =
	'settled' | 'pending' | 'failed' | 'cancelled' | 'awaiting_approval' | 'approved' | 'rejected';

/** Coarse family. `order` is the only one carrying asset + quantity. */
export type TransactionType = 'payment' | 'order' | 'fee' | 'transfer' | 'income';

/** How the money physically moved. Null for anything that isn't a cash movement. */
export type PaymentMethod = 'bank_transfer' | 'direct_debit' | 'cheque' | 'internal' | 'card';

/**
 * How an uncategorised pension payment-in was resolved.
 *
 * ONE definition, re-exported. This module and `book/contributions/types.ts` each declared the
 * same five members — the fixture-era copy here and the one mirroring the API's CHECK
 * constraint there — which is the duplication `clientName` was consolidated to avoid. The
 * contributions module owns it now because that is where the vocabulary is enforced (the DB
 * constraint, `app/contributions.py::CATEGORIES` and the Morgan Lloyd mapping all key off it);
 * this path keeps working so no caller has to move.
 *
 * Safe from a cycle: `contributions/types.ts` is types and const arrays only, with no imports.
 */
export type { ContributionCategory } from '../contributions/types.js';
export { CONTRIBUTION_CATEGORIES } from '../contributions/types.js';

import type { ContributionCategory } from '../contributions/types.js';
import { formatMoney } from '../../../money.js';
import { sentenceCase } from '../../../sentence-case.js';

export interface Transaction {
	uid: string;
	/** ISO date (yyyy-mm-dd) — the value date, not the booking timestamp. */
	date: string;
	account_id: number;
	account_name: string;
	account_uid: string;
	client_id: number;
	/** Public/provider client reference shown beneath the name. Older fixture rows may omit it. */
	client_uid?: string | null;
	client_name: string;
	adviser: string | null;
	/** Provider code of the account's custodian. The listing filter matches this, not the display name. */
	custodian?: string | null;
	type: TransactionType;
	subtype: string;
	method: PaymentMethod | null;
	/** Signed: money out of the account is negative. */
	amount: number;
	/** Orders only. */
	asset: string | null;
	/** The security's ISIN. Searchable since the ledger was built and never SHOWN until now — which
	 *  meant the one identifier that is unambiguous across custodians could be found and not read. */
	isin?: string | null;
	/** Orders only — the units bought or sold. */
	quantity: number | null;
	/** Dealing price per unit, in the movement's currency. Absent on a cash movement. */
	price?: number | null;
	/** Charges on the movement. Signed: a charge taken is negative. Absent when there were none.
	 *  This stays the TOTAL; dealing_charges / other_charges say what it was made of. */
	fee_amount?: number | null;
	dealing_charges?: number | null;
	other_charges?: number | null;
	/** What KIND of charge, in the custodian's words ('Platform Fee', 'Adviser Fee', …). */
	fee_type?: string | null;
	/** Tax on the movement, signed the same way as the fee. Absent when there was none. */
	tax_amount?: number | null;
	book_cost?: number | null;
	realised_gain?: number | null;
	equalisation_payment?: number | null;
	subscription_amount?: number | null;
	report_group_kind?: string | null;
	/** The statement family, when the API has derived it (`statement.family`). */
	family?: string | null;
	/** The API's signed figure (`statement.signed_amount`). */
	signed_amount?: number | null;
	/** Cumulative total as at this row, on an account-scoped ledger. */
	running_total?: number | null;
	/** When the entitlement was struck: execution date on an order, ex-date on income. */
	trade_date?: string | null;
	transaction_date?: string | null;
	report_group?: string | null;
	/** Cash that settled, when the custodian stated a net beside the figure on the row. */
	settlement_amount?: number | null;
	/** The movement's own currency. Usually the account's, and worth stating where it is not:
	 *  a figure with no currency beside it is only safe on a book that has exactly one. */
	currency?: string | null;
	/** The same figure in the account currency, when that currency differs. Absent when the
	 *  row is already in the account currency, or the custodian gave no rate. */
	base_amount?: number | null;
	/** The account's currency — the ISO code on the conversion line. */
	base_currency?: string | null;
	/** ISO date the custodian settled the movement, when it said. */
	settlement_date?: string | null;
	/** Populated custodian fields. Keys are the payload's own; blanks and zeros are already gone. */
	record?: Record<string, string> | null;
	status: TransactionStatus;
	product_type: string;
	/**
	 * A payment into a pension/SIPP whose contribution type isn't known yet. Relief at source
	 * depends on it, so it can't just default — someone has to say.
	 */
	needs_categorisation?: boolean;
	category?: ContributionCategory;
}

/**
 * The muted second line under a foreign amount: the same figure in the account currency.
 *
 * Rendered with `Money`, so the pence step back exactly as they do on every other figure.
 * Nothing is shown when the movement is already in the account currency, the amount is zero,
 * or the custodian gave no rate.
 */
export function conversionCaption(
	row: Pick<Transaction, 'amount' | 'currency' | 'base_amount' | 'base_currency'>
): { amount: number; currency: string; toString: () => string } | null {
	const code = row.base_currency?.trim().toUpperCase();
	if (row.base_amount == null || !code || !row.amount) return null;
	if ((row.currency ?? 'GBP').trim().toUpperCase() === code) return null;
	const amount = row.base_amount;
	// `toString` is the figure itself. A template that prints the pair — rather than handing
	// `amount` and `currency` to `Money` — would otherwise show "[object Object]".
	return {
		amount,
		currency: code,
		toString() {
			return formatMoney(amount, code);
		}
	};
}

/**
 * Graphene origin kinds written as `source_subtype` before the listing learned to print them as
 * words. A snake_case key in a Type cell is an internal name leaking onto a statement.
 *
 * The same list as `app/instruction_kinds.py::KIND_LABEL`. Rows written after that change already
 * store the word, so this only covers what is still on the book as a key.
 */
export const ORIGIN_KIND_LABEL: Record<string, string> = {
	expected_in: 'Payment',
	withdrawal: 'Withdrawal',
	adhoc_fee: 'Adviser fee',
	model_topup: 'Model top-up',
	model_withdrawal: 'Model withdrawal',
	personal_contribution: 'Personal contribution',
	employer_contribution: 'Employer contribution',
	third_party_contribution: 'Third party contribution',
	contribution: 'Contribution',
	transfer: 'Transfer in',
	transfer_in: 'Transfer in'
};

/**
 * The vendor's own word for a movement, when it says more than the canonical type already does.
 *
 * Type and Subtype were two columns for one classification, and on this book most rows had the SAME
 * word in both — "Dividend · Dividend", "Withdrawal · Withdrawal" — so a whole column was spent
 * restating its neighbour. Folded together, the canonical type leads and this is the detail beneath
 * it; where the two agree there is nothing to show and the cell is one line.
 *
 * Compared on letters and digits only, lowercased, so the words are matched rather than their
 * punctuation: the canonical `transfer_in` and a vendor `Transfer In` are the same statement, and
 * showing both would put the redundancy back one row at a time. `Platform Fee` against `fee` is NOT
 * redundant — it names which fee — so it survives, which is the whole reason the field is kept.
 */
export function subtypeDetail(t: {
	// A STRING, not `TransactionType`. All this function does with the type is compare its letters
	// against the subtype's, and the words it is folding are the CUSTODIAN's — "Dividend",
	// "Transfer In", "Withdrawal" — which is what the canonical five are mapped FROM. Binding the
	// parameter to the narrow union said the comparison only holds for a canonical value, which is
	// the opposite of the case it exists for. `Transaction.type` stays the union.
	type?: string | null;
	subtype?: string | null;
}): string | null {
	const subtype = (t.subtype ?? '').trim();
	if (!subtype) return null;
	const labelled = ORIGIN_KIND_LABEL[subtype] ?? sentenceCase(subtype);
	const letters = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '');
	if (letters(labelled) === letters(t.type ?? '')) return null;
	// Sentence-cased, because the canonical type beside it always is (`humanise`) and the custodian's
	// own word is whatever it happened to send: the ledger read "Deposit: cash receipt" on one row
	// and "Deposit: Cash Receipt" on the next, which looks like two different things.
	//
	// `sentenceCase` only touches the first letter and turns `_ - .` into spaces, so "Platform Fee"
	// and "At Best" keep the vendor's own capitalisation, and `expected_in` no longer prints as
	// `Expected_in`.
	return labelled;
}

/** Product types where a payment in must be categorised before it can be reported.
 *
 * **`TPPENSION` was missing here and present in all three Python copies**
 * (`contributions.PENSION_PRODUCTS`, `matching.PENSION_PRODUCTS`), which is the worst shape for a
 * vocabulary duplicated across a language boundary: the API agreed a third-party pension IS one and
 * every portal surface disagreed. So drawdown, the contributions gate, the MPAA allowance, the
 * client record's pension section and a beneficiary nomination were all dark for every `TPPENSION`
 * account — and they are REAL pensions (Seccl's third-party wrapper; the dev book holds five, and
 * NO `SIPP` rows at all, which is why "a client with a SIPP" showed no pension sections).
 *
 * A separate and unfixed gap in the same area: 51 accounts on the dev book carry `product_id` NULL
 * while being named "Pension", "Pension Drawdown", "Drawdown" or "SIPP". Nothing here can help
 * them — a product is what makes a wrapper a pension, and inferring one from a display NAME is the
 * rule this estate refuses (`docs/platform-model.md`). They need the provider pipeline to resolve a
 * product parent; until it does they gate as "not a pension" everywhere. */
export const PENSION_PRODUCTS = new Set(['SIPP', 'PENSION', 'SSAS', 'TPPENSION']);

/**
 * True for the pension wrappers. Drawdown, contribution categorisation and illustrations all
 * hang off this: they're meaningless on a GIA or ISA. Lives beside PENSION_PRODUCTS so "is
 * this a pension?" stays decided in exactly one place (was apps/platform `$lib/accounts/product`).
 */
export function isPensionProduct(productType: string | null | undefined): boolean {
	return !!productType && PENSION_PRODUCTS.has(productType.toUpperCase());
}

/** Payments into a pension that nobody has categorised yet — the Action required queue. */
export function needsAction(transactions: Transaction[]): Transaction[] {
	return transactions.filter(
		(t) =>
			t.needs_categorisation &&
			!t.category &&
			t.amount > 0 &&
			PENSION_PRODUCTS.has(t.product_type.toUpperCase())
	);
}

/** One line of the charge breakdown. */
export type ChargeLine = { label: string; amount: number };

/**
 * What a movement's charge was made of, as the lines worth SHOWING.
 *
 * "What did this trade cost me?" is the DEALING charge, and it used to be folded into one
 * `fee_amount` alongside the platform's own monthly fee — one figure answering two questions. So the
 * split is stated when it says something the total does not.
 *
 * A component whose whole charge IS the total contributes no line: repeating the same number under a
 * narrower label reads as a second charge, which on a client's statement is worse than saying less.
 * Same for a zero — a nil dealing charge is not a fact about the trade.
 */
export function chargeBreakdown(
	transaction: Pick<Transaction, 'fee_amount' | 'dealing_charges' | 'other_charges'>
): ChargeLine[] {
	const total = transaction.fee_amount ?? null;
	const parts: ChargeLine[] = [];
	for (const [label, value] of [
		['Dealing', transaction.dealing_charges],
		['Other', transaction.other_charges]
	] as const) {
		if (value === null || value === undefined || value === 0) continue;
		// The whole charge under a narrower name is not a breakdown.
		if (total !== null && value === total) continue;
		parts.push({ label, amount: value });
	}
	return parts;
}

/**
 * What `trade_date` should be CALLED on this row.
 *
 * One column, two meanings, and both are wanted: on an order it is the execution date, on income it
 * is the EX-DATE — the date that decides whether the holder was entitled at all, which is what makes
 * a dividend auditable against the units held then. Naming it one thing would be wrong half the time.
 */
export function entitlementLabel(transaction: Pick<Transaction, 'type' | 'family'>): string {
	const family = transaction.family ?? transaction.type;
	return family === 'income' || family === 'dividend' || family === 'interest'
		? 'Ex-date'
		: 'Traded';
}
