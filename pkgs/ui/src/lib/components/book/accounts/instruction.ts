/**
 * Payment, withdrawal and ad hoc fee drawers on the account record.
 *
 * The POST stays app-side (each portal has its own same-origin proxy). What lives here is the
 * money those drawers state — account value, available cash, remaining as the amount is typed —
 * the destination bank a withdrawal is paid to, and the placeholder pay-in details the Payment
 * drawer shows until a per-custodian collection account is wired.
 *
 * Value is `accounts.value` (the header Stat), not custodian cash. Available cash still nets off
 * pending withdrawals, model top-ups and ad hoc fees, the same claim `funding.committedByAccount`
 * already counts, so this figure and the API's refusal cannot disagree. `expected_in` spends
 * nothing and is never credited. The gate stays on cash, never on value: paying away the portfolio
 * as if it were cash is the defect `instructions.py` exists to prevent.
 */
import { formatSortCode } from '../../../forms/editor/masks.js';
import { bankSchemeOf } from '../clients/bank.js';
import { sentenceCase } from '../../../sentence-case.js';
import type { SelectMenuGroup, SelectMenuItems } from '../../ui/select-menu/index.js';
import type { FeePackageOption } from './types.js';
import { providerReference } from '../clients/format.js';
import { committedByAccount } from '../clients/funding.js';
import { asNumber } from '../clients/lake.js';
import type { ContributionCategory } from '../contributions/types.js';
import type { BankDetails, InstructionKind, InstructionRow } from '../clients/types.js';
import type { WithdrawalFees } from './types.js';
import { resolvedBank, usesClientBank } from './bank.js';

/** The kinds the account record can raise. Invest/Sell stay on the client record. */
export type CashInstructionKind = Extract<
	InstructionKind,
	'expected_in' | 'withdrawal' | 'adhoc_fee'
>;

/** A regular payment or withdrawal on the account record — a schedule, not a cash instruction. */
export type RegularScheduleKind = 'regular_payment' | 'regular_withdrawal';

/** Everything the account record's money drawer can open as. */
export type AccountSheetKind = CashInstructionKind | RegularScheduleKind;

export const isCashInstruction = (kind: string | null): kind is CashInstructionKind =>
	kind === 'expected_in' || kind === 'withdrawal' || kind === 'adhoc_fee';

export const isRegularSchedule = (kind: string | null): kind is RegularScheduleKind =>
	kind === 'regular_payment' || kind === 'regular_withdrawal';

export const isAccountSheetKind = (kind: string | null): kind is AccountSheetKind =>
	isCashInstruction(kind) || isRegularSchedule(kind);

/** Payment (one-off or regular). SIPP contribution type and the adviser-fee switch live here. */
export const isPayInSheet = (kind: string | null): boolean =>
	kind === 'expected_in' || kind === 'regular_payment';

export const isWithdrawSheet = (kind: string | null): boolean =>
	kind === 'withdrawal' || kind === 'regular_withdrawal';

/** Withdrawal and ad hoc fee spend cash; Payment does not. A regular schedule spends nothing now. */
export const spendsCash = (kind: string | null): boolean =>
	kind === 'withdrawal' || kind === 'adhoc_fee';

export const CASH_INSTRUCTION_TITLE: Record<CashInstructionKind, string> = {
	expected_in: 'Payment',
	withdrawal: 'Withdraw',
	adhoc_fee: 'Ad hoc fee'
};

export const SCHEDULE_TITLE: Record<RegularScheduleKind, string> = {
	regular_payment: 'Add payment',
	regular_withdrawal: 'Add withdrawal'
};

/** A SIPP does not withdraw cash from the custodian — it draws down at the scheme administrator. */
export function accountSheetTitle(kind: AccountSheetKind, isPension = false): string {
	if (isPension && kind === 'withdrawal') return 'Drawdown';
	return isRegularSchedule(kind) ? SCHEDULE_TITLE[kind] : CASH_INSTRUCTION_TITLE[kind];
}

/** The custodian's collection sort code, formatted for display. The account name, sort code and
 *  account number are the custodian's pay-in bank account, configured on the firm's platform
 *  settings and carried on the account record; blank until the firm has set them. */
export function payInSortCode(raw: string | null | undefined): string {
	const value = raw?.trim();
	return value ? formatSortCode(value) : '';
}

/** The payment reference the client quotes so the credit matches this account. Prefer the
 *  custodian's own account number (SEI's, for a SEI account); fall back to the stored platform
 *  identifier, then the book uid with its display prefix stripped. */
export function payInReference(
	custodianAccountNumber: string | null | undefined,
	platformIdentifier: string | null | undefined,
	uid: string
): string {
	const fromCustodian = custodianAccountNumber?.trim() || platformIdentifier?.trim();
	if (fromCustodian) return fromCustodian;
	return providerReference(uid) ?? uid;
}

/** Local calendar day as `YYYY-MM-DD`. Expected On is a calendar date, so UTC midnight must not
 *  move it into yesterday. */
export function calendarDate(at: Date = new Date()): string {
	const y = at.getFullYear();
	const m = String(at.getMonth() + 1).padStart(2, '0');
	const d = String(at.getDate()).padStart(2, '0');
	return `${y}-${m}-${d}`;
}

/** A filled Expected On that is before today. Blank is not past — the date is optional. */
export function isPastDate(iso: string, today: string = calendarDate()): boolean {
	return iso !== '' && iso < today;
}

/** How far back a payment in may be BACKDATED, in days — mirrors `app/instructions.py::BACKDATE_LIMIT_DAYS`. */
export const BACKDATE_LIMIT_DAYS = 365;

/** The earliest Expected On a payment in may carry: today minus the backdating limit, as a
 *  calendar date. */
export function backdateFloor(today: string = calendarDate()): string {
	const [y = 0, m = 1, d = 1] = today.split('-').map(Number);
	const at = new Date(y, m - 1, d);
	at.setDate(at.getDate() - BACKDATE_LIMIT_DAYS);
	return calendarDate(at);
}

/** What is still cash after the typed amount (and, for a withdrawal, its rail fee — see
 *  `withdrawalMethodFee`). Null/non-finite amount spends nothing, so remaining equals available
 *  until they type. May go negative — that is how over-available reads, and it is deliberately
 *  the FEE-INCLUSIVE figure: a withdrawal that exactly exhausts the cash leg before the fee is
 *  still one that would overdraw once the rail takes its cut. */
export function remainingCash(
	available: number,
	amount: number | null | undefined,
	fee: number = 0
): number {
	const typed = typeof amount === 'number' && Number.isFinite(amount) ? amount : 0;
	return available - typed - fee;
}

/** Custodian cash and what is still free after pending cash claims. The drawer labels the first
 *  figure Value and binds `accounts.value` instead; this is the cash the remaining line nets. */
export function withdrawalCash(
	accountId: number,
	bankBalance: number,
	instructions: InstructionRow[]
): { bankBalance: number; available: number } {
	const cash = asNumber(bankBalance);
	const committed = committedByAccount(instructions).get(accountId)?.cash ?? 0;
	return { bankBalance: cash, available: Math.max(cash - committed, 0) };
}

export type WithdrawalBankSource = 'account' | 'client';

/** Where a withdrawal is paid: the account's own `[0]`, else the client's. */
export function withdrawalDestination(account: {
	bank_accounts?: unknown;
	client: { bank_account?: unknown };
}): { bank: BankDetails | null; source: WithdrawalBankSource | null } {
	const bank = resolvedBank(account);
	if (!bank) return { bank: null, source: null };
	return { bank, source: usesClientBank(account) ? 'client' : 'account' };
}

export function withdrawalBankCaption(source: WithdrawalBankSource | null): string {
	if (source === 'account') return 'This account’s bank details.';
	if (source === 'client') return 'This account uses the client’s bank details.';
	return 'No bank details recorded for this account or the client.';
}

/** Whose details the withdrawal will be paid to — the account’s own, or the client’s. */
export function withdrawalBankHeading(source: WithdrawalBankSource): string {
	return source === 'client' ? 'Client bank account details' : 'Account bank account details';
}

/** The rail a withdrawal actually pays over — the same rule `app/bank_accounts.py::sei_payment_method`
 *  states server-side: an explicit `method` wins, otherwise the RAIL decides. An international
 *  (IBAN) destination has nothing to choose — it always reaches SEI over SWIFT — and a UK one
 *  defaults to BACS unless Faster Payment was chosen. No destination at all defaults to BACS,
 *  which is what "no fee" should read as rather than an unexplained blank. */
export type WithdrawalPaymentMethod = 'bacs' | 'faster_payment' | 'swift';

export function withdrawalPaymentMethod(bank: BankDetails | null): WithdrawalPaymentMethod {
	if (!bank) return 'bacs';
	if (bankSchemeOf(bank) === 'iban') return 'swift';
	return bank.method === 'faster_payment' ? 'faster_payment' : 'bacs';
}

/** The UK choice a withdrawal starts on. International destinations have nothing to choose. */
export function ukWithdrawalMethod(bank: BankDetails | null): 'bacs' | 'faster_payment' {
	return withdrawalPaymentMethod(bank) === 'faster_payment' ? 'faster_payment' : 'bacs';
}

export const WITHDRAWAL_METHOD_LABEL: Record<WithdrawalPaymentMethod, string> = {
	bacs: 'BACS',
	faster_payment: 'Faster Payment',
	swift: 'SWIFT'
};

/** Fallback rail fees, used only where the account read has none (an older API, or a mount —
 *  a test double, say — that never fetched `withdrawal_fees`). SEI's actual, current rates are
 *  configured server-side (`app/config.py::sei_faster_payment_fee`/`sei_swift_fee`, the same for
 *  every firm) and travel down on `AccountDetailData.withdrawal_fees`; these are today's
 *  published rates, kept as the last-resort answer rather than a blank one. BACS is free, which
 *  is the whole reason it is the default, and is never configured — there is nowhere else in the
 *  estate (`platform.fee_packages` is advisory/investment charges, a different thing entirely)
 *  a rail fee like this could come from instead. */
export const WITHDRAWAL_METHOD_FEE: Record<WithdrawalPaymentMethod, number> = {
	bacs: 0,
	faster_payment: 0.5,
	swift: 15
};

/** The provider's charge for one rail. Null means this custodian has no tariff (not SEI).
 *  Undefined is an older read that did not say, so the published rate stands in. */
export function railFeeForMethod(
	method: WithdrawalPaymentMethod,
	fees?: WithdrawalFees | null
): number {
	if (method === 'bacs') return 0;
	if (fees === null) return 0;
	return fees?.[method] ?? WITHDRAWAL_METHOD_FEE[method];
}

export function withdrawalMethodFee(
	bank: BankDetails | null,
	fees?: WithdrawalFees | null
): number {
	return railFeeForMethod(withdrawalPaymentMethod(bank), fees);
}

/** What is taken from the client's cash. The firm can pay the tariff instead, per withdrawal. */
export function clientBorneRailFee(tariff: number, passOn: boolean): number {
	return passOn ? tariff : 0;
}

/** The debit the portal gate should compare with available cash. An unrecognised method, or a
 *  withdrawal that did not name one, is just the amount: the API still applies the saved default. */
export function withdrawalDebit(
	amount: number,
	method: string | null | undefined,
	fees: WithdrawalFees | null | undefined,
	passOn: boolean
): number {
	if (method !== 'bacs' && method !== 'faster_payment' && method !== 'swift') return amount;
	return withdrawalTotalDebit(amount, clientBorneRailFee(railFeeForMethod(method, fees), passOn));
}

/** Amount + fee, rounded to the penny — added as floats (`amount` is `MoneyInput`'s plain major-unit
 *  number), so `1000 + 0.5` needs rounding back to `1000.5` rather than trusting the binary result. */
export function withdrawalTotalDebit(amount: number | null | undefined, fee: number): number {
	const typed = typeof amount === 'number' && Number.isFinite(amount) ? amount : 0;
	return Math.round((typed + fee) * 100) / 100;
}

/** What lands in the recipient's account: the amount asked for, whole. The rail fee is charged
 *  ON TOP of the withdrawal (`withdrawalTotalDebit`) or borne by the firm — it is never taken out
 *  of the sum the client receives. Stated beside Total to be debited so nobody has to subtract. */
export function withdrawalRecipientReceives(amount: number | null | undefined): number {
	const typed = typeof amount === 'number' && Number.isFinite(amount) ? amount : 0;
	return Math.round(typed * 100) / 100;
}

export type InstructionSubmitBody = {
	kind: CashInstructionKind;
	account_id: number;
	amount: number;
	model_id: null;
	/** Which of the firm's fee packages an ad hoc fee is charged under. Always null from this
	 *  drawer now — the type picker is gone; a value the API is still given is stored. */
	fee_package_id: number | null;
	due_date: string | null;
	justification?: string;
	/** The rail this withdrawal pays over. Omitted on every other kind. */
	payment_method?: WithdrawalPaymentMethod;
	/** When false the firm pays the rail fee. Omitted means the fee is passed on. */
	pass_rail_fee?: boolean;
	/** A pension payment. Personal, employer, or third party. Selects the treatment code. */
	category?: ContributionCategory;
	/** Adviser charge taken from this payment or withdrawal. Omitted when the switch is off. */
	adviser_fee?: number;
	/** Which of the firm's fee packages the adviser fee is charged under. SEI needs a fee TYPE for
	 *  it, so it is required on a SEI account and omitted for Seccl (its fee leg is amount-only). */
	adviser_fee_package_id?: number;
	/** Sell assets to fund a withdrawal (SEI raise-cash / Seccl Proportional). Omitted on other
	 *  kinds; false means the withdrawal needs settled cash. */
	auto_disinvest?: boolean;
	/** Invest a pay-in into the account's model once the cash lands (the inverse of
	 *  `auto_disinvest`). Default true; false leaves the money as cash. Omitted on other kinds. */
	auto_invest?: boolean;
};

export type RegularSubmitBody = {
	kind: RegularScheduleKind;
	amount: number;
	frequency: string;
	next_date: string | null;
	/** Why the schedule changes — present when four-eye is on for the account's movements. */
	justification?: string;
};

export type SheetSubmitBody = InstructionSubmitBody | RegularSubmitBody;

/** What lands after an optional adviser fee is taken off a payment. Rounded to the penny —
 *  subtracted as floats the same way `withdrawalTotalDebit` adds. */
export function netPayment(
	amount: number | null | undefined,
	fee: number | null | undefined
): number {
	const typed = typeof amount === 'number' && Number.isFinite(amount) ? amount : 0;
	const charge = typeof fee === 'number' && Number.isFinite(fee) ? fee : 0;
	return Math.round((typed - charge) * 100) / 100;
}

/**
 * The Ad hoc fee drawer's fee-type options: the FIRM'S OWN fee register, grouped by its own
 * categories.
 *
 * A firm running an ongoing adviser charge, an investment-management charge and an initial charge
 * has three names for what a one-off £250 might be, and the amount alone records none of them.
 * The names come from `platform.fee_packages` rather than an enum of ours, because a fee type the
 * firm has not configured is one it does not charge, and a list we invented would disagree with
 * the catalogue the same firm edits.
 *
 * The PRICE is deliberately absent from the label, unlike `feePackageOptionLabel` on the journey
 * kickoff picker: this charge is the amount the adviser types, so "Ongoing adviser — 0.75%"
 * beside a £250 box says something untrue about the money.
 *
 * Grouping only when the register spans more than one heading — a single heading over every option
 * is a line that narrows nothing. An uncategorised package is its own heading rather than being
 * folded into a neighbour's, because "the firm has not said" is a different answer from "adviser".
 * Groups keep the order the packages arrive in, which is the firm's own `sort_order`, rather than
 * being re-sorted alphabetically behind its back.
 */
export function feeTypeItems(packages: FeePackageOption[]): SelectMenuItems {
	const groups: SelectMenuGroup[] = [];
	for (const fee of packages) {
		const category = (fee.category ?? '').trim();
		const label = category ? sentenceCase(category) : 'Other';
		let group = groups.find((g) => g.label === label);
		if (!group) {
			group = { label, options: [] };
			groups.push(group);
		}
		group.options.push({ value: String(fee.id), label: fee.name });
	}
	return groups.length < 2 ? groups.flatMap((g) => g.options) : groups;
}
