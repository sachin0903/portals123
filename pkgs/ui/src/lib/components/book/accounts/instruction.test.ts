import { describe, expect, it } from 'vitest';
import type { InstructionRow } from '../clients/types.js';
import type { FeePackageOption } from './types.js';
import {
	BACKDATE_LIMIT_DAYS,
	CASH_INSTRUCTION_TITLE,
	accountSheetTitle,
	backdateFloor,
	calendarDate,
	feeTypeItems,
	isAccountSheetKind,
	isCashInstruction,
	isPastDate,
	isPayInSheet,
	isRegularSchedule,
	isWithdrawSheet,
	netPayment,
	payInReference,
	payInSortCode,
	remainingCash,
	spendsCash,
	WITHDRAWAL_METHOD_FEE,
	WITHDRAWAL_METHOD_LABEL,
	withdrawalBankCaption,
	withdrawalBankHeading,
	withdrawalCash,
	withdrawalDestination,
	clientBorneRailFee,
	ukWithdrawalMethod,
	withdrawalDebit,
	withdrawalMethodFee,
	withdrawalPaymentMethod,
	withdrawalRecipientReceives,
	withdrawalTotalDebit
} from './instruction.js';

const pending = (over: Partial<InstructionRow> = {}): InstructionRow =>
	({
		uid: 'ins_x',
		kind: 'withdrawal',
		amount: 250,
		status: 'pending',
		due_date: null,
		note: null,
		created_at: '2026-07-01T00:00:00Z',
		account_id: 7,
		account_uid: 'acc_7',
		account_name: 'ISA',
		model_name: null,
		...over
	}) as InstructionRow;

describe('pay-in details', () => {
	it('formats the configured custodian sort code the way the bank fields do, blank when unset', () => {
		expect(payInSortCode('123456')).toBe('12 - 34 - 56');
		expect(payInSortCode('12-34-56')).toBe('12 - 34 - 56');
		expect(payInSortCode(null)).toBe('');
		expect(payInSortCode('  ')).toBe('');
	});

	it('quotes the SEI account number, then the platform id, then the uid with its prefix stripped', () => {
		expect(payInReference('SEI123', 'plat_9', 'sei_AXF5AC13E')).toBe('SEI123');
		expect(payInReference(null, 'plat_9', 'sei_AXF5AC13E')).toBe('plat_9');
		expect(payInReference('  ', '  ', 'seccl_AXF5AC13E')).toBe('AXF5AC13E');
		expect(payInReference(null, null, 'acc_12ab34cd')).toBe('acc_12ab34cd');
	});
});

describe('Expected On', () => {
	it('is the local calendar day, zero-padded', () => {
		expect(calendarDate(new Date(2026, 8, 12, 15, 0, 0))).toBe('2026-09-12');
		expect(calendarDate(new Date(2026, 0, 5, 0, 0, 0))).toBe('2026-01-05');
	});

	it('treats today as allowed and yesterday as past; blank is not past', () => {
		expect(isPastDate('2026-09-12', '2026-09-12')).toBe(false);
		expect(isPastDate('2026-09-13', '2026-09-12')).toBe(false);
		expect(isPastDate('2026-09-11', '2026-09-12')).toBe(true);
		expect(isPastDate('', '2026-09-12')).toBe(false);
	});

	it('may be backdated as far as a year — the floor is a calendar date, so it survives a leap day', () => {
		expect(BACKDATE_LIMIT_DAYS).toBe(365);
		expect(backdateFloor('2026-09-12')).toBe('2025-09-12');
		expect(backdateFloor('2028-03-01')).toBe('2027-03-02');
		expect(backdateFloor('2026-01-01')).toBe('2025-01-01');
	});
});

describe('withdrawalRecipientReceives', () => {
	it('is the amount asked for, whole — the fee is on top of it or with the firm, never out of it', () => {
		expect(withdrawalRecipientReceives(1_000)).toBe(1_000);
		expect(withdrawalRecipientReceives(999.999)).toBe(1_000);
		expect(withdrawalRecipientReceives(null)).toBe(0);
		expect(withdrawalRecipientReceives(undefined)).toBe(0);
		expect(withdrawalTotalDebit(1_000, 0.5) - withdrawalRecipientReceives(1_000)).toBeCloseTo(0.5);
	});
});

describe('isCashInstruction / spendsCash', () => {
	it('is Payment, Withdrawal and Ad hoc fee — Invest and Sell stay on the client record', () => {
		expect(isCashInstruction('expected_in')).toBe(true);
		expect(isCashInstruction('withdrawal')).toBe(true);
		expect(isCashInstruction('adhoc_fee')).toBe(true);
		expect(isCashInstruction('model_topup')).toBe(false);
		expect(isCashInstruction('model_withdrawal')).toBe(false);
		expect(isCashInstruction(null)).toBe(false);
	});

	it('only Withdrawal and Ad hoc fee spend cash', () => {
		expect(spendsCash('withdrawal')).toBe(true);
		expect(spendsCash('adhoc_fee')).toBe(true);
		expect(spendsCash('expected_in')).toBe(false);
		expect(spendsCash('regular_payment')).toBe(false);
		expect(spendsCash('regular_withdrawal')).toBe(false);
		expect(spendsCash('model_topup')).toBe(false);
	});

	it('names the three drawers', () => {
		expect(CASH_INSTRUCTION_TITLE.expected_in).toBe('Payment');
		expect(CASH_INSTRUCTION_TITLE.withdrawal).toBe('Withdraw');
		expect(CASH_INSTRUCTION_TITLE.adhoc_fee).toBe('Ad hoc fee');
	});

	it('titles a SIPP withdrawal as Drawdown, and leaves every other kind unchanged', () => {
		expect(accountSheetTitle('withdrawal')).toBe('Withdraw');
		expect(accountSheetTitle('withdrawal', true)).toBe('Drawdown');
		expect(accountSheetTitle('expected_in', true)).toBe('Payment');
		expect(accountSheetTitle('regular_withdrawal', true)).toBe('Add withdrawal');
		expect(accountSheetTitle('adhoc_fee', true)).toBe('Ad hoc fee');
	});

	it('treats a regular schedule as a drawer, not a cash instruction', () => {
		expect(isRegularSchedule('regular_payment')).toBe(true);
		expect(isRegularSchedule('regular_withdrawal')).toBe(true);
		expect(isAccountSheetKind('regular_payment')).toBe(true);
		expect(isCashInstruction('regular_payment')).toBe(false);
		expect(isPayInSheet('expected_in')).toBe(true);
		expect(isPayInSheet('regular_payment')).toBe(true);
		expect(isWithdrawSheet('withdrawal')).toBe(true);
		expect(isWithdrawSheet('regular_withdrawal')).toBe(true);
		expect(isPayInSheet('adhoc_fee')).toBe(false);
	});
});

describe('remainingCash', () => {
	it('equals available until an amount is typed', () => {
		expect(remainingCash(1_000, null)).toBe(1_000);
		expect(remainingCash(1_000, undefined)).toBe(1_000);
		expect(remainingCash(1_000, Number.NaN)).toBe(1_000);
	});

	it('falls as the amount is typed, and goes negative when the request is over available', () => {
		expect(remainingCash(1_000, 250)).toBe(750);
		expect(remainingCash(1_000, 1_000)).toBe(0);
		expect(remainingCash(1_000, 1_200)).toBe(-200);
	});

	it('also nets a rail fee, so exhausting the cash leg before the fee still goes negative', () => {
		expect(remainingCash(1_000, 1_000, 0.5)).toBeCloseTo(-0.5);
		expect(remainingCash(1_000.5, 1_000, 0.5)).toBe(0);
	});

	it('defaults an omitted fee to zero, unchanged from before the fee argument existed', () => {
		expect(remainingCash(1_000, 500)).toBe(500);
	});
});

describe('withdrawalPaymentMethod / withdrawalMethodFee', () => {
	it('reads BACS — free — from no destination at all', () => {
		expect(withdrawalPaymentMethod(null)).toBe('bacs');
		expect(withdrawalMethodFee(null)).toBe(0);
	});

	it('reads BACS from a UK destination with no method chosen', () => {
		const bank = { name: 'A', sort_code: '12-34-56', account_number: '12345678' };
		expect(withdrawalPaymentMethod(bank)).toBe('bacs');
		expect(withdrawalMethodFee(bank)).toBe(0);
	});

	it('reads Faster Payment — £0.50 — when that is what is stored', () => {
		const bank = {
			name: 'A',
			sort_code: '12-34-56',
			account_number: '12345678',
			method: 'faster_payment' as const
		};
		expect(withdrawalPaymentMethod(bank)).toBe('faster_payment');
		expect(withdrawalMethodFee(bank)).toBe(0.5);
	});

	it('reads SWIFT — £15.00 — from an international destination, with nothing to choose', () => {
		const bank = { name: 'A', iban: 'GB82WEST12345698765432', bic: 'BARCGB22' };
		expect(withdrawalPaymentMethod(bank)).toBe('swift');
		expect(withdrawalMethodFee(bank)).toBe(15);
	});

	it('matches the stated fee schedule and labels', () => {
		expect(WITHDRAWAL_METHOD_FEE).toEqual({ bacs: 0, faster_payment: 0.5, swift: 15 });
		expect(WITHDRAWAL_METHOD_LABEL).toEqual({
			bacs: 'BACS',
			faster_payment: 'Faster Payment',
			swift: 'SWIFT'
		});
	});

	it('a configured fee schedule overrides the built-in default — same rate for every firm', () => {
		const fasterPayment = {
			name: 'A',
			sort_code: '12-34-56',
			account_number: '12345678',
			method: 'faster_payment' as const
		};
		const swift = { name: 'A', iban: 'GB82WEST12345698765432', bic: 'BARCGB22' };
		expect(withdrawalMethodFee(fasterPayment, { faster_payment: 0.75, swift: 20 })).toBe(0.75);
		expect(withdrawalMethodFee(swift, { faster_payment: 0.75, swift: 20 })).toBe(20);
	});

	it('BACS is never configurable — always free, whatever the fee schedule says', () => {
		const bank = { name: 'A', sort_code: '12-34-56', account_number: '12345678' };
		// A schedule with no bacs key at all still resolves BACS to zero rather than undefined.
		expect(withdrawalMethodFee(bank, { faster_payment: 0.75, swift: 20 })).toBe(0);
	});

	it('falls back to the built-in default for a fee schedule missing a key', () => {
		const bank = {
			name: 'A',
			sort_code: '12-34-56',
			account_number: '12345678',
			method: 'faster_payment' as const
		};
		expect(
			withdrawalMethodFee(bank, {} as unknown as { faster_payment: number; swift: number })
		).toBe(0.5);
	});

	it('starts a UK withdrawal on the saved method, and lets the firm pay the fee', () => {
		const faster = {
			name: 'A',
			sort_code: '12-34-56',
			account_number: '12345678',
			method: 'faster_payment' as const
		};
		expect(ukWithdrawalMethod(faster)).toBe('faster_payment');
		expect(ukWithdrawalMethod({ name: 'A', sort_code: '12-34-56', account_number: '1' })).toBe(
			'bacs'
		);
		expect(clientBorneRailFee(0.5, true)).toBe(0.5);
		expect(clientBorneRailFee(0.5, false)).toBe(0);
		expect(withdrawalDebit(2000, 'faster_payment', { faster_payment: 0.5, swift: 15 }, true)).toBe(
			2000.5
		);
		expect(withdrawalDebit(2000, 'faster_payment', { faster_payment: 0.5, swift: 15 }, false)).toBe(
			2000
		);
		expect(withdrawalDebit(2000, 'bacs', { faster_payment: 0.5, swift: 15 }, true)).toBe(2000);
		expect(withdrawalDebit(2000, null, { faster_payment: 0.5, swift: 15 }, true)).toBe(2000);
	});

	it('charges nothing when the API explicitly says this custodian has no SEI tariff', () => {
		const bank = {
			name: 'A',
			sort_code: '12-34-56',
			account_number: '12345678',
			method: 'faster_payment'
		};
		expect(withdrawalMethodFee(bank, null)).toBe(0);
	});
});

describe('netPayment', () => {
	it('is the typed amount until a fee is entered', () => {
		expect(netPayment(150_000, null)).toBe(150_000);
		expect(netPayment(150_000, undefined)).toBe(150_000);
		expect(netPayment(null, 250)).toBe(-250);
	});

	it('is the payment after the adviser fee, per the ticket’s own example', () => {
		expect(netPayment(150_000, 2_970.81)).toBe(147_029.19);
	});

	it('rounds away float noise from subtracting pence', () => {
		expect(netPayment(1_000.5, 0.5)).toBe(1_000);
	});
});

describe('withdrawalTotalDebit', () => {
	it('adds the fee onto the typed amount, per the ticket’s own example', () => {
		expect(withdrawalTotalDebit(1_000, 0.5)).toBe(1_000.5);
	});

	it('adds the SWIFT fee', () => {
		expect(withdrawalTotalDebit(1_000, 15)).toBe(1_015);
	});

	it('rounds away float noise from adding pence to a large amount', () => {
		expect(withdrawalTotalDebit(999.99, 0.5)).toBe(1_000.49);
	});

	it('treats a blank amount as zero, so the total is just the fee', () => {
		expect(withdrawalTotalDebit(null, 0.5)).toBe(0.5);
		expect(withdrawalTotalDebit(undefined, 15)).toBe(15);
	});
});

describe('withdrawalCash', () => {
	it('nets pending cash claims off the bank balance, the way the API’s gate does', () => {
		expect(withdrawalCash(7, 1_000, [pending({ amount: 250 })])).toEqual({
			bankBalance: 1_000,
			available: 750
		});
	});

	it('does not credit expected-in, and ignores settled or other-account rows', () => {
		expect(
			withdrawalCash(7, 1_000, [
				pending({ kind: 'expected_in', amount: 500 }),
				pending({ status: 'settled', amount: 400 }),
				pending({ account_id: 9, amount: 400 })
			])
		).toEqual({ bankBalance: 1_000, available: 1_000 });
	});

	it('treats a model top-up as a cash claim, same as a withdrawal', () => {
		expect(withdrawalCash(7, 1_000, [pending({ kind: 'model_topup', amount: 200 })])).toEqual({
			bankBalance: 1_000,
			available: 800
		});
	});

	it('treats an ad hoc fee as a cash claim, same as a withdrawal', () => {
		expect(withdrawalCash(7, 1_000, [pending({ kind: 'adhoc_fee', amount: 200 })])).toEqual({
			bankBalance: 1_000,
			available: 800
		});
	});
});

describe('withdrawalDestination', () => {
	const clientBank = { name: 'M Oakden', sort_code: '04-00-04', account_number: '12345678' };
	const own = { name: 'Oakden SIPP', sort_code: '11-11-11', account_number: '11111111' };

	it('prefers the account’s own details', () => {
		expect(
			withdrawalDestination({
				bank_accounts: [own],
				client: { bank_account: clientBank }
			})
		).toEqual({
			bank: {
				name: 'Oakden SIPP',
				bank: null,
				sort_code: '11-11-11',
				account_number: '11111111',
				iban: null,
				bic: null,
				method: null
			},
			source: 'account'
		});
		expect(withdrawalBankCaption('account')).toBe('This account’s bank details.');
		expect(withdrawalBankHeading('account')).toBe('Account bank account details');
	});

	it('falls back to the client when the account has none of its own', () => {
		expect(
			withdrawalDestination({
				bank_accounts: null,
				client: { bank_account: clientBank }
			}).source
		).toBe('client');
		expect(withdrawalBankCaption('client')).toBe('This account uses the client’s bank details.');
		expect(withdrawalBankHeading('client')).toBe('Client bank account details');
	});

	it('says so when neither side has details', () => {
		expect(
			withdrawalDestination({
				bank_accounts: null,
				client: { bank_account: null }
			})
		).toEqual({ bank: null, source: null });
		expect(withdrawalBankCaption(null)).toBe(
			'No bank details recorded for this account or the client.'
		);
	});
});

describe('the ad hoc fee’s type options', () => {
	const fee = (over: Partial<FeePackageOption> & { id: number; name: string }): FeePackageOption =>
		over;

	it('is the firm’s own register, flat while there is one category to speak of', () => {
		expect(
			feeTypeItems([
				fee({ id: 4, name: 'Ongoing adviser charge', category: 'adviser' }),
				fee({ id: 9, name: 'Initial advice charge', category: 'adviser' })
			])
		).toEqual([
			{ value: '4', label: 'Ongoing adviser charge' },
			{ value: '9', label: 'Initial advice charge' }
		]);
	});

	it('groups by the firm’s own categories once there is more than one', () => {
		expect(
			feeTypeItems([
				fee({ id: 4, name: 'Ongoing adviser charge', category: 'adviser' }),
				fee({ id: 7, name: 'Investment management', category: 'discretionary' }),
				fee({ id: 9, name: 'Initial advice charge', category: 'adviser' })
			])
		).toEqual([
			{
				label: 'Adviser',
				options: [
					{ value: '4', label: 'Ongoing adviser charge' },
					{ value: '9', label: 'Initial advice charge' }
				]
			},
			{ label: 'Discretionary', options: [{ value: '7', label: 'Investment management' }] }
		]);
	});

	it('keeps the firm’s own order rather than sorting the groups behind its back', () => {
		const items = feeTypeItems([
			fee({ id: 1, name: 'Zed charge', category: 'zed' }),
			fee({ id: 2, name: 'Adviser charge', category: 'adviser' })
		]);
		expect(items.map((g) => ('label' in g ? g.label : ''))).toEqual(['Zed', 'Adviser']);
	});

	it('names an uncategorised package rather than dropping it', () => {
		expect(
			feeTypeItems([
				fee({ id: 1, name: 'Platform charge', category: 'platform' }),
				fee({ id: 2, name: 'Something else', category: null }),
				fee({ id: 3, name: 'Blank', category: '  ' })
			])
		).toEqual([
			{ label: 'Platform', options: [{ value: '1', label: 'Platform charge' }] },
			{
				label: 'Other',
				options: [
					{ value: '2', label: 'Something else' },
					{ value: '3', label: 'Blank' }
				]
			}
		]);
	});

	it('never puts the PRICE on a label — the amount is the one the adviser types', () => {
		const items = feeTypeItems([
			fee({ id: 4, name: 'Ongoing adviser charge', fee_type: 'percentage', category: 'adviser' })
		]);
		expect(JSON.stringify(items)).not.toContain('%');
	});

	it('is empty for a firm with no register, which is what makes the picker optional', () => {
		expect(feeTypeItems([])).toEqual([]);
	});
});
