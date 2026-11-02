/**
 * The Payment / Withdrawal / Ad hoc fee drawer’s SHAPE — what it asks and, more importantly,
 * what it does not.
 *
 * A SOURCE SCAN, because the drawer’s body renders through a portal that SSR does not run:
 * `render()` of a Sheet returns empty markup, which makes every “does not contain” assertion pass
 * for the wrong reason. Same posture as `contribution-form.test.ts`.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(
	fileURLToPath(new URL('./instruction-sheet.svelte', import.meta.url)),
	'utf8'
);
const template = source
	.slice(source.indexOf('</script>'))
	.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\/[^\n]*/g, '');
const saveFn = source.slice(source.indexOf('async function save()'), source.indexOf('</script>'));

describe('the instruction drawer', () => {
	it('does not ask for a note — a reason is asked only when that action requires one', () => {
		expect(template).not.toMatch(/\bNote\b/);
		expect(saveFn).not.toContain('note');
		expect(template).toContain('{#if needsJustification}');
		expect(template).toContain('Why this instruction');
		expect(saveFn).toContain('justification');
	});

	it('does not ask a date on a one-off payment in — SEI expects it open-ended', () => {
		// The optional "Expected On" date is gone from one-off pay-ins: a blank due date tells SEI
		// to expect the money open-ended and reconcile the credit when it lands. A regular schedule
		// still carries its next run date. due_date rides the instruction (null for the one-off).
		expect(template).not.toContain('Expected On');
		expect(template).not.toContain('This payment is backdated');
		expect(template).toContain('>Next date</Label>');
		expect(saveFn).toContain('due_date');
	});

	it('tells the client to pay into the bank account, with collection details and a reference', () => {
		expect(template).toContain('The client needs to pay this amount into the bank account below');
		expect(template).not.toContain('Placeholder bank details.');
		// The custodian's collection account comes from the firm's platform settings, carried on
		// the account record; the reference is the custodian's own (SEI) account number.
		expect(template).toContain('Account name');
		expect(template).toContain('Sort code');
		expect(template).toContain('Account number');
		expect(template).toContain('Reference');
		expect(source).toContain('payInSortCode(payInAccountSortCode)');
		expect(template).toContain("bankRow('Account number', payInAccountNumber");
		expect(source).toContain('payInReference(custodianAccountNumber, platformIdentifier');
	});

	it('titles the payment box Custodian bank details, values on the right', () => {
		expect(template).toContain('variant="info"');
		expect(template).not.toContain('InfoIcon');
		expect(template).toContain('Custodian bank details');
		expect(template).toContain("'Copy sort code', true");
		expect(template).toContain('justify-end');
		expect(template).toContain('whitespace-nowrap');
		expect(template).toContain('truncate');
		expect(template).not.toContain('break-all');
	});

	it('states value, available cash, and remaining cash as the amount is typed', () => {
		expect(template).toContain('>Value</dt>');
		expect(template).not.toContain('Bank balance');
		expect(template).toContain('Available cash');
		expect(template).toContain('Remaining cash');
		expect(source).toContain(
			'remainingCash(position.available, amount, fee + withdrawalAdviserFee)'
		);
		expect(source).toContain('accountValue ?? 0');
		expect(template).toContain('space-y-1.5 text-sm');
		expect(template).toContain('gap-3 text-sm');
	});

	it('states the payment method fee and the total to be debited for a withdrawal only', () => {
		expect(template).toContain('Total to be debited');
		expect(source).toContain('railFeeForMethod');
		expect(source).toContain('withdrawalTotalDebit');
		expect(template).toContain('Pass this fee on to the client');
		// The fee row is labelled by the method itself ("Faster Payment fee"), not a generic
		// "Payment method" row — that row is filtered out of the destination details.
		expect(template).toContain('{feeMethodLabel} fee');
		expect(source).toContain("label !== 'Payment method'");
		expect(source).toContain('ukWithdrawalMethod(destination.bank)');
	});

	it('states what the recipient receives beside the total to be debited', () => {
		// The fee sits on top of the withdrawal, or with the firm — the recipient always gets
		// the amount asked for. Both lines, so nobody has to subtract.
		expect(template).toContain('Recipient receives');
		expect(source).toContain('withdrawalRecipientReceives(amount)');
		const debited = template.indexOf('Total to be debited');
		const receives = template.indexOf('Recipient receives');
		expect(receives).toBeGreaterThan(debited);
		expect(receives - debited).toBeLessThan(400);
	});

	it('lets the pay-to bank details be changed from the Withdraw drawer, through the account door', () => {
		// The same fields and the same endpoint as the overview pencil
		// (`PUT …/bank-accounts`, four-eye included) — but without closing the form first.
		expect(source).toContain('onSaveBank');
		expect(source).toContain('<BankAccountFields');
		expect(source).toContain('accountBankSaveBody(checked.value, bankAccounts)');
		expect(source).toContain('validateBank(');
		expect(template).toContain('Add bank details');
		expect(template).toContain('idPrefix="account-instruction-bank-"');
		expect(template).toContain('{#if bankReviewRequired}');
		expect(source).toContain("bankError = 'Say why this change is needed.'");
		expect(source).toContain('!editingBank &&');
	});

	it('takes the fee schedule as a prop, so a configured rate overrides the built-in default', () => {
		expect(source).toContain('withdrawalFees,');
		expect(source).toContain('withdrawalFees?: WithdrawalFees | null');
	});

	it('shows the withdrawal destination bank above the amount, and not for an ad hoc fee', () => {
		expect(template).not.toContain('Paid to');
		expect(template).not.toContain('Client bank account details');
		expect(template).not.toContain('Account bank account details');
		expect(source).not.toContain('withdrawalBankHeading');
		expect(source).toContain('withdrawalDestination');
		expect(source).toContain('withdrawalBankCaption');
		expect(source).toContain('isWithdrawSheet');
		expect(source).toContain('adhoc_fee');
		expect(template).toContain('Charge a one-off fee');
		expect(template).toContain('Withdrawals will be transferred to the following bank account.');
		expect(template).toContain('w-full italic text-pretty');
		expect(template).toMatch(/bankRow\(row\[0\], row\[1\], null, true\)/);
		expect(source).toContain("label !== 'Payment method'");
		// For a withdrawal the destination bank is shown first — before the amount — so the adviser
		// sees where the money goes before typing how much.
		const bank = template.indexOf('Withdrawals will be transferred to the following bank account.');
		const amount = template.indexOf('id="account-instruction-amount"');
		expect(bank).toBeLessThan(amount);
	});

	it('asks which fee an ad hoc fee is on a SEI account (type picker), amount still typed', () => {
		// SEI charges a one-time fee against a fee TYPE, so a SEI ad hoc fee needs a type picker
		// (built from the firm's one-off packages); Seccl's ad hoc charge stays amount-only.
		expect(source).toContain('feeTypeItems');
		expect(template).toContain('Fee type');
		expect(source).toContain("kind === 'adhoc_fee' && custodian === 'sei'");
		expect(template).toContain("{kind === 'adhoc_fee' ? 'Fee' : 'Amount'}");
		// The chosen type rides the instruction only for an ad hoc fee; other kinds send null.
		expect(saveFn).toContain("next === 'adhoc_fee' && feePackageId ? Number(feePackageId) : null");
	});

	it('offers an adviser fee on a payment as its own section at the foot, and states the net after it', () => {
		expect(template).toContain('>Adviser fee</p>');
		expect(template).toContain('Include Adviser Fee');
		expect(template).toContain('id="account-instruction-adviser-fee"');
		expect(template).toContain('Net Payment');
		expect(source).toContain('netPayment(amount, chargedFee)');
		expect(source).toContain('isPayInSheet');
		expect(saveFn).toContain('adviser_fee: chargedFee');
		expect(source).toContain('feeTooLarge');
		expect(template).toContain('The adviser fee must be less than the payment.');
		expect(template).toContain('border-border space-y-3 border-t pt-4');
		expect(template).toContain('flex items-center gap-3');
		expect(template.indexOf('id="account-instruction-amount"')).toBeLessThan(
			template.indexOf('>Adviser fee</p>')
		);
		expect(template.indexOf('Relief at source')).toBeLessThan(template.indexOf('>Adviser fee</p>'));
	});

	it('asks contribution type on a SIPP payment, and shows relief at source below the amount', () => {
		expect(template).toContain('Contribution type');
		expect(template).toContain('CREATABLE_CONTRIBUTION_CATEGORIES');
		expect(template).toContain('<GrossUp');
		expect(source).toContain("category === 'personal_contribution'");
		expect(template).toContain('Relief at source');
		expect(template).not.toContain('Relief paid in advance');
		expect(template).not.toContain('HMRC checks');
		expect(template).not.toContain('EligibilityList');
		expect(template.indexOf('Contribution type')).toBeLessThan(
			template.indexOf('Relief at source')
		);
		expect(template.indexOf('id="account-instruction-amount"')).toBeLessThan(
			template.indexOf('Relief at source')
		);
	});

	it('does not withhold a SIPP withdrawal behind Morgan Lloyd / CTC copy', () => {
		// The account record starts a DCube session instead of opening this drawer. The cash
		// form stays here for GIA/ISA withdrawals; the dead-end alert that used to sit in
		// front of it told the adviser to use a different button, so it is gone.
		expect(source).not.toContain('sippDrawdown');
		expect(template).not.toContain('Morgan Lloyd and CTC');
		expect(template).not.toContain(
			'Taking money from a SIPP is a drawdown, not a cash withdrawal.'
		);
		expect(template).not.toContain('Use Drawdown on the account to start a session.');
		expect(source).toContain('accountSheetTitle(kind, isPension)');
		expect(template).toContain('id="account-instruction-amount"');
		expect(saveFn).not.toContain('sippDrawdown');
	});

	it('asks frequency on a regular payment or withdrawal, monthly / quarterly / yearly', () => {
		expect(template).toContain('>Frequency</Label>');
		expect(source).toContain('SCHEDULE_FREQUENCIES');
		expect(source).toContain('isRegularSchedule');
		expect(template).toContain('Next date');
	});

	it('does not hold a regular schedule for a justification the drawer does not ask', () => {
		// `justificationRequired` is the reason switch on the money action that sends a cash
		// instruction. Add payment writes the account's
		// movements, so the why-box is cash-instruction only — requiring it on
		// `ready` is how a complete regular form stayed grey.
		expect(source).toContain('needsJustification');
		expect(source).toContain('justificationRequired && isCashInstruction(kind)');
		expect(source).toContain('(!needsJustification || Boolean(justification.trim()))');
		expect(saveFn).toContain('needsJustification && !justification.trim()');
		expect(saveFn).not.toContain('if (justificationRequired && !justification.trim())');
	});
});
