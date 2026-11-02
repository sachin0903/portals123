import { describe, expect, it } from 'vitest';
import { accountBankSaveBody, ownBankAccounts, resolvedBank, usesClientBank } from './bank.js';

const clientBank = { name: 'M Oakden', sort_code: '04-00-04', account_number: '12345678' };
const own = { name: 'Oakden SIPP', sort_code: '11-11-11', account_number: '11111111' };

describe('ownBankAccounts', () => {
	it('treats null, a missing key and an empty list as none of its own', () => {
		expect(ownBankAccounts(null)).toEqual([]);
		expect(ownBankAccounts(undefined)).toEqual([]);
		expect(ownBankAccounts([])).toEqual([]);
		expect(ownBankAccounts({ name: 'nope' })).toEqual([]);
	});

	it('keeps objects in the stored order — [0] is the default a payment takes', () => {
		expect(ownBankAccounts([own, clientBank])).toEqual([own, clientBank]);
	});
});

describe('usesClientBank / resolvedBank', () => {
	it('falls back to the client when the account has none of its own', () => {
		const account = { bank_accounts: null, client: { bank_account: clientBank } };
		expect(usesClientBank(account)).toBe(true);
		expect(resolvedBank(account)).toEqual({
			name: 'M Oakden',
			bank: null,
			sort_code: '04-00-04',
			account_number: '12345678',
			iban: null,
			bic: null,
			method: null
		});
	});

	it('prefers the account’s [0] over the client, even when both are filled', () => {
		const account = {
			bank_accounts: [own],
			client: { bank_account: clientBank }
		};
		expect(usesClientBank(account)).toBe(false);
		expect(resolvedBank(account)?.sort_code).toBe('11-11-11');
		expect(resolvedBank(account)?.name).toBe('Oakden SIPP');
	});
});

describe('accountBankSaveBody', () => {
	it('wraps a single object as a list of one — the API refuses a bare object', () => {
		expect(accountBankSaveBody(own, null)).toEqual({ bank_accounts: [own] });
	});

	it('clears the whole list so the account falls back to the client', () => {
		expect(accountBankSaveBody(null, [own])).toEqual({ bank_accounts: null });
	});

	it('replaces [0] and keeps the rest — this editor does not collect a second profile', () => {
		const second = { name: 'Income', sort_code: '22-22-22', account_number: '22222222' };
		expect(accountBankSaveBody(own, [clientBank, second])).toEqual({
			bank_accounts: [own, second]
		});
	});

	it('keeps the vendor passthrough on [0] so a name change does not drop the profile id', () => {
		expect(
			accountBankSaveBody(own, [
				{ ...own, name: 'Old', provider_ref: 'payto-9', direction: 'pay_to' }
			])
		).toEqual({
			bank_accounts: [
				{
					provider_ref: 'payto-9',
					direction: 'pay_to',
					...own
				}
			]
		});
	});

	it('clears a stale Faster Payment choice rather than leaking it back in — `method` is now a first-class field this editor collects, not a passthrough one', () => {
		const withMethod = accountBankSaveBody(own, [
			{ ...own, name: 'Old', method: 'faster_payment', provider_ref: 'payto-9' }
		]);
		expect(withMethod).toEqual({
			bank_accounts: [{ provider_ref: 'payto-9', ...own }]
		});
		expect((withMethod.bank_accounts?.[0] as Record<string, unknown>).method).toBeUndefined();
	});

	it('keeps a Faster Payment choice the editor itself collected', () => {
		const withMethod = accountBankSaveBody({ ...own, method: 'faster_payment' }, [
			{ ...own, name: 'Old' }
		]);
		expect((withMethod.bank_accounts?.[0] as Record<string, unknown>).method).toBe(
			'faster_payment'
		);
	});

	it('preserves a wider provider-authored rail this two-choice editor cannot reinterpret', () => {
		const withMethod = accountBankSaveBody(own, [
			{ ...own, name: 'Old', method: 'chaps', provider_ref: 'payto-9' }
		]);
		expect(withMethod.bank_accounts?.[0]).toMatchObject({
			method: 'chaps',
			provider_ref: 'payto-9'
		});
	});
});
