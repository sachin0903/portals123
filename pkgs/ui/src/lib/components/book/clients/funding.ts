/**
 * What a client's accounts can actually fund — the money classification behind the record's
 * money-movement dialogs AND the portal-side withdrawal gate (the platform's `?/createInstruction`
 * re-derives this before any POST; the API enforces it again server-side).
 *
 * Extracted from `apps/platform/src/routes/clients/[id]/+page.server.ts` so the admin portal's
 * loaders can state the SAME headroom figures the platform states — two derivations of "available
 * cash" is how the same £2,000 gets promised away twice.
 */
import { asNumber, classifyHolding, type RawHolding } from './lake.js';
import type { InstructionKind, InstructionRow } from './types.js';

/** What an account can fund, from the provider's latest holdings snapshot. `cash` is the
 *  cash/money-market leg; `invested` is everything else (what a model disinvestment can come
 *  from). This is the ONLY cash figure the system holds — there is no provider cash ledger — so
 *  the withdrawal checks validate against it. */
export type FundingBasis = { cash: number; invested: number; asOf: string | null };

/** Split a contact's provider holdings into each account's fundable legs. */
export function basisByAccount(rows: RawHolding[]): Map<number, FundingBasis> {
	const out = new Map<number, FundingBasis>();
	for (const h of rows) {
		const id = typeof h.account_id === 'number' ? h.account_id : null;
		if (id === null) continue;
		const b = out.get(id) ?? { cash: 0, invested: 0, asOf: null };
		const value = asNumber(h.value);
		if (classifyHolding(h) === 'Cash') b.cash += value;
		else b.invested += value;
		const asOf = h.as_of ? String(h.as_of) : null;
		if (asOf && (!b.asOf || asOf > b.asOf)) b.asOf = asOf;
		out.set(id, b);
	}
	return out;
}

/** The same map `basisByAccount` builds, from a holdings-summary's per-account legs. */
export function basisFromSummaryLegs(
	legs: { accountId: number; cash: number; invested: number; asOf: string | null }[]
): Map<number, FundingBasis> {
	return new Map(
		legs.map((leg) => [leg.accountId, { cash: leg.cash, invested: leg.invested, asOf: leg.asOf }])
	);
}

/** What an account's *pending* instructions have already claimed off each leg. Nothing settles an
 *  instruction, so a pending row is money promised away: a top-up buys into the model out of cash
 *  (so it draws on the cash leg, like a withdrawal), an ad hoc fee spends cash the same way, a
 *  model withdrawal draws on what is invested, and `expected_in` — unsettled money — is never
 *  credited to either. Mirrors `instructions._LEG`. */
export type Committed = { cash: number; invested: number };

/** Which leg of the position each instruction kind spends. `expected_in` spends nothing and is
 *  never gated: money coming in is unconstrained by what is held. Mirrors `instructions._LEG`. */
export const INSTRUCTION_LEG: Partial<Record<InstructionKind, 'cash' | 'invested'>> = {
	withdrawal: 'cash',
	adhoc_fee: 'cash',
	model_topup: 'cash',
	model_withdrawal: 'invested'
};

export function committedByAccount(rows: InstructionRow[]): Map<number, Committed> {
	const out = new Map<number, Committed>();
	for (const i of rows) {
		if (i.status !== 'pending') continue;
		const leg = INSTRUCTION_LEG[i.kind];
		if (!leg) continue;
		const c = out.get(i.account_id) ?? { cash: 0, invested: 0 };
		// A rail fee the firm has chosen to pay is not a claim on the client's cash.
		const borne = i.kind === 'withdrawal' && i.pass_rail_fee !== false ? asNumber(i.rail_fee) : 0;
		c[leg] += asNumber(i.amount) + borne;
		out.set(i.account_id, c);
	}
	return out;
}

/** One account's fundable money: what the provider holds on each leg, what pending instructions have
 *  already claimed, and therefore what is actually still available. Showing the gross leg as
 *  "available" is what let the same £2,000 be promised away twice. */
export type AccountFunding = FundingBasis & {
	accountId: number;
	committedCash: number;
	committedInvested: number;
	availableCash: number;
	availableInvested: number;
};

export function accountFunding(
	basis: Map<number, FundingBasis>,
	committed: Map<number, Committed>
): AccountFunding[] {
	const ids = new Set([...basis.keys(), ...committed.keys()]);
	return [...ids].map((accountId) => {
		const b = basis.get(accountId) ?? { cash: 0, invested: 0, asOf: null };
		const c = committed.get(accountId) ?? { cash: 0, invested: 0 };
		return {
			accountId,
			...b,
			committedCash: c.cash,
			committedInvested: c.invested,
			availableCash: Math.max(b.cash - c.cash, 0),
			availableInvested: Math.max(b.invested - c.invested, 0)
		};
	});
}
