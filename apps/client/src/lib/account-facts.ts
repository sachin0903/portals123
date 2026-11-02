/**
 * The facts row on a client's account page — the tiles under the header, decided here so the
 * set is table-testable and never a single tile.
 *
 * The row used to be CONDITIONAL on wrapper-specific facts (allowance, pension split), and for a
 * GIA that left "Cash" alone on a two-column grid, with no second fact to read it against. A
 * figure for cash is only useful beside what the rest of the money is doing, so every account
 * gets the same three: Cash · Invested · Gain/loss. The wrapper-specific tiles follow them — the
 * ISA/JISA allowance (`allowanceFact`: the custodian's figure, or the statutory annual limit
 * when the custodian is silent) and a pension's crystallised/uncrystallised split.
 */
import {
	allowanceFact,
	consolidatedCash,
	formatGBP,
	formatSignedMoney,
	pensionSplit
} from '@graphene/ui';
import type { Account, Holding } from '$lib/api';
import { isCashHolding } from '$lib/holdings';

/** Self-serve money follows the account, not the person's book. A client can be advised on one
 *  account and execution-only on another; only the execution-only one offers payment and
 *  withdrawal. Anything else offers nothing — the shell already carries the message button. */
export function accountHeaderActions(
	serviceType: string | null | undefined
): 'deposit_withdraw' | 'none' {
	return serviceType === 'execution_only' ? 'deposit_withdraw' : 'none';
}

/** Is this an account at all yet? A DRAFT is an application in progress — no holdings, no
 *  transactions, usually no name (it shows by its product, "Seccl GIA", or its uid) — and nothing
 *  else in the portal lists one: the home and accounts pages read the API's valued set and the
 *  listing toolbars drop drafts (`page-data.ts`). Closed accounts remain: they have history. */
export function isOpenAccount(account: { status?: string | null }): boolean {
	return (account.status ?? '').trim().toLowerCase() !== 'draft';
}

/** The accounts a transfer page may offer: never a draft, and then — a book with
 *  `money_movement` may use any of them; without it, only the execution-only ones, so the advised
 *  accounts in a mixed book stay out. */
export function selfServeAccounts<
	T extends { service_type?: string | null; status?: string | null }
>(accounts: readonly T[], moneyMovement: boolean): T[] {
	const open = accounts.filter(isOpenAccount);
	if (moneyMovement) return open;
	return open.filter((account) => account.service_type === 'execution_only');
}

export type AccountFact = {
	label: string;
	value: string;
	sub?: string;
	valueTone?: 'positive' | 'negative';
};

export function accountFacts(
	account: Account,
	holdings: readonly Pick<Holding, 'asset_type' | 'asset_name' | 'isin' | 'value'>[],
	now: Date = new Date()
): AccountFact[] {
	const facts: AccountFact[] = [];

	// Cash as the lake states it, plus any scheme-administrator balance. When the account has no
	// cash figure at all (a row the lake has not valued) the holdings' own cash rows stand in.
	const cash =
		account.cash == null
			? { total: holdings.filter(isCashHolding).reduce((s, h) => s + (h.value ?? 0), 0) }
			: consolidatedCash(account.cash, account.provider_positions);
	facts.push({ label: 'Cash', value: formatGBP(cash.total), sub: cash.breakdown });

	// What is in the market: the valuation less the cash in it. The count is of INVESTMENTS, so a
	// cash row the lake lists as a holding is not "1 holding".
	const investments = holdings.filter((h) => !isCashHolding(h));
	const invested =
		account.value != null
			? Math.max(0, account.value - cash.total)
			: investments.reduce((s, h) => s + (h.value ?? 0), 0);
	facts.push({
		label: 'Invested',
		value: formatGBP(invested),
		sub: investments.length
			? `${investments.length} ${investments.length === 1 ? 'holding' : 'holdings'}`
			: undefined
	});

	// The account's own unrealised figure — never a sum of the holdings' gains, which are struck
	// elsewhere and would put a second answer on the same screen. "—" when the lake has none, and
	// "—" when the figure cannot be right: a gain is value less what was paid, so a gain at or
	// above the whole valuation means a cost of nothing or less — a bad basis, the same reading
	// the API's `_holding_return` clamp makes per holding. Saying nothing beats "+£1.58m" on a
	// £452k account, which is what one dev extract produced.
	const raw = account.growth ?? null;
	const growth = raw != null && account.value != null && raw >= account.value ? null : raw;
	facts.push({
		label: 'Gain / loss',
		value: growth == null ? '—' : formatSignedMoney(growth),
		sub: growth == null ? undefined : 'On what you hold today',
		valueTone: growth == null || growth === 0 ? undefined : growth > 0 ? 'positive' : 'negative'
	});

	const allowance = allowanceFact(account, now);
	if (allowance) {
		facts.push({
			label: allowance.label,
			value: formatGBP(allowance.value),
			sub: allowance.sub
		});
	}

	const split = pensionSplit(account.provider_positions);
	if (split) {
		facts.push(
			{ label: 'Crystallised', value: formatGBP(split.crystallised) },
			{ label: 'Uncrystallised', value: formatGBP(split.uncrystallised) }
		);
	}

	return facts;
}
