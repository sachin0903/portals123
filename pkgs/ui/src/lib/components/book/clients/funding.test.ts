/** The money classification behind the record's money dialogs and the portal-side withdrawal gate.
 *
 *  Moved from `apps/platform/src/routes/clients/[id]/page.server.test.ts` when the pure helpers
 *  were extracted into the shared book components (the `?/createInstruction` action tests — the
 *  refusals themselves — stay in the app, where the action lives).
 */
import { describe, expect, it } from 'vitest';
import { accountFunding, basisByAccount, committedByAccount } from './funding';
import type { InstructionRow } from './types';

const ACCOUNT = 7;
const CASH = 2_000;
const INVESTED = 48_000;

/** A pending instruction on ACCOUNT unless told otherwise. */
const pending = (over: Partial<InstructionRow> = {}): InstructionRow =>
	({
		uid: 'ins_x',
		kind: 'withdrawal',
		amount: CASH,
		status: 'pending',
		due_date: null,
		note: null,
		created_at: '2026-07-01T00:00:00Z',
		account_id: ACCOUNT,
		account_name: 'ISA',
		model_name: null,
		...over
	}) as InstructionRow;

/** One account's provider position: £2,000 cash (a money-market fund whose asset_type is the
 *  lake's coarse "Stock"), £48,000 invested. */
const POSITION = [
	{
		account_id: ACCOUNT,
		asset_name: 'BlackRock ICS Sterling Liquidity Fund',
		asset_type: 'Stock',
		value: CASH,
		as_of: '2026-06-30'
	},
	{
		account_id: ACCOUNT,
		asset_name: 'Vanguard FTSE Global All Cap',
		asset_type: 'Stock',
		value: INVESTED,
		as_of: '2026-07-01'
	}
];

describe('basisByAccount', () => {
	it('splits the cash leg from what is invested, per account', () => {
		const b = basisByAccount(POSITION).get(ACCOUNT)!;
		expect(b.cash).toBe(CASH);
		expect(b.invested).toBe(INVESTED);
		expect(b.asOf).toBe('2026-07-01'); // the latest snapshot across the rows
	});

	it('recognises cash by fund name, not just by the provider asset_type', () => {
		// The lake's asset_type is coarse ("Stock"): a money-market fund is cash only because the
		// NAME says so. If this stops matching, every withdrawal is refused as £0 available.
		const rows = [
			{ account_id: 1, asset_name: 'Cash GBP', asset_type: 'Cash', value: 10 },
			{ account_id: 1, asset_name: 'Sterling Money Market Fund', asset_type: 'Stock', value: 20 },
			{ account_id: 1, asset_name: 'Global Liquidity Portfolio', asset_type: 'Stock', value: 30 },
			{ account_id: 1, asset_name: 'iShares Core FTSE 100', asset_type: 'Stock', value: 40 }
		];
		const b = basisByAccount(rows).get(1)!;
		expect(b.cash).toBe(60);
		expect(b.invested).toBe(40);
	});

	it('does not count a fund whose name merely contains "cash" as cash', () => {
		// A bare substring test made an equity fund called "Cashel …" part of the balance a withdrawal
		// may be paid from, so a fund manager's naming choice inflated the withdrawable cash. Mirrors
		// apps/api/app/instructions.py::is_cash_holding.
		const rows = [
			{
				account_id: 1,
				asset_name: 'Cashel Capital Global Equity Fund',
				asset_type: 'Stock',
				value: 90
			},
			{ account_id: 1, asset_name: 'Liquidityplus Equity Income', asset_type: 'Stock', value: 10 }
		];
		const b = basisByAccount(rows).get(1)!;
		expect(b.cash).toBe(0);
		expect(b.invested).toBe(100);
	});

	it('keeps accounts apart and ignores rows with no account', () => {
		const rows = [
			{ account_id: 1, asset_name: 'Cash', value: 5 },
			{ account_id: 2, asset_name: 'Cash', value: 7 },
			{ account_id: null, asset_name: 'Cash', value: 999 }
		];
		const basis = basisByAccount(rows);
		expect(basis.get(1)!.cash).toBe(5);
		expect(basis.get(2)!.cash).toBe(7);
		expect(basis.size).toBe(2);
	});
});

describe('committedByAccount / accountFunding', () => {
	it('counts only pending rows, per account, on the leg each kind spends', () => {
		const c = committedByAccount([
			pending({ kind: 'withdrawal', amount: 500, rail_fee: 0.5 }),
			pending({ kind: 'model_topup', amount: 300 }), // buys into the model out of cash
			pending({ kind: 'adhoc_fee', amount: 50 }),
			pending({ kind: 'model_withdrawal', amount: 1_000 }),
			pending({ kind: 'expected_in', amount: 99_999 }), // unsettled money is never a claim
			pending({ kind: 'withdrawal', amount: 700, status: 'cancelled' }),
			pending({ kind: 'withdrawal', amount: 800, status: 'settled' }),
			pending({ kind: 'withdrawal', amount: 400, account_id: 99 })
		]);
		expect(c.get(ACCOUNT)).toEqual({ cash: 850.5, invested: 1_000 });
		expect(c.get(99)).toEqual({ cash: 400, invested: 0 });
	});

	it('does not reserve a rail fee the firm has chosen to pay', () => {
		const c = committedByAccount([
			pending({ kind: 'withdrawal', amount: 500, rail_fee: 0.5, pass_rail_fee: false })
		]);
		expect(c.get(ACCOUNT)).toEqual({ cash: 500, invested: 0 });
	});

	it('nets the claims off the provider legs and never reports a negative', () => {
		const f = accountFunding(
			basisByAccount(POSITION),
			committedByAccount([pending({ amount: CASH + 5_000 })])
		)[0];
		expect(f.cash).toBe(CASH); // what the provider holds is stated as-is...
		expect(f.committedCash).toBe(CASH + 5_000);
		expect(f.availableCash).toBe(0); // ...and what is spendable is floored at zero
		expect(f.availableInvested).toBe(INVESTED);
	});
});
