import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import BankAccountCheckStatus from './bank-account-check-status.svelte';
import {
	BANK_CONFIRMED_NOTE,
	BANK_CHECKING_MESSAGE,
	BANK_UNAVAILABLE_MESSAGE,
	type BankCheckResult
} from './types.js';

const confirmed: BankCheckResult = {
	valid: true,
	state: 'valid',
	sort_code: '10-00-00',
	account_number: '31510604',
	bank_name: 'BANK OF ENGLAND',
	branch_name: 'HEAD OFFICE',
	bank_bic: 'BKENGB33',
	supports: { bacs: true, faster_payments: false, chaps: true, direct_debits: false },
	message: ''
};

describe('BankAccountCheckStatus', () => {
	it('names the bank and the rails the directory confirmed', () => {
		const { body } = render(BankAccountCheckStatus, {
			props: { status: 'confirmed', result: confirmed }
		});
		expect(body).toContain('BANK OF ENGLAND, HEAD OFFICE');
		expect(body).toContain('title="BANK OF ENGLAND"');
		expect(body).toContain(BANK_CONFIRMED_NOTE);
		expect(body).toContain('BACS');
		expect(body).toContain('CHAPS');
		expect(body).not.toContain('Faster Payments');
		expect(body).not.toContain('Direct Debit');
	});

	it('draws the bank’s logo beside the name the directory confirmed', () => {
		const { body } = render(BankAccountCheckStatus, {
			props: {
				status: 'confirmed',
				result: {
					...confirmed,
					bank_name: 'MONZO BANK LIMITED',
					branch_name: 'MONZO BANK LIMITED',
					bank_bic: 'MONZGB2L'
				}
			}
		});
		expect(body).toContain('logos/banks/GB/monz/round.svg');
		expect(body).toContain('alt="MONZO BANK LIMITED"');
	});

	it('shows the directory’s reason when the pair is rejected', () => {
		const { body } = render(BankAccountCheckStatus, {
			props: {
				status: 'rejected',
				result: {
					...confirmed,
					valid: false,
					state: 'sort_code_unknown',
					message: "This sort code isn't recognised."
				}
			}
		});
		expect(body).toContain("This sort code isn't recognised.");
		expect(body).toContain('role="alert"');
	});

	it('says the check is in progress', () => {
		const { body } = render(BankAccountCheckStatus, { props: { status: 'checking' } });
		expect(body).toContain(BANK_CHECKING_MESSAGE);
	});

	it('lets a save continue when the directory cannot be reached', () => {
		const { body } = render(BankAccountCheckStatus, { props: { status: 'unavailable' } });
		expect(body).toContain(BANK_UNAVAILABLE_MESSAGE);
	});

	it('renders nothing until both numbers are complete', () => {
		const { body } = render(BankAccountCheckStatus, { props: { status: 'idle' } });
		expect(body).not.toContain(BANK_CHECKING_MESSAGE);
		expect(body).not.toContain(BANK_CONFIRMED_NOTE);
		expect(body).not.toContain(BANK_UNAVAILABLE_MESSAGE);
	});
});
