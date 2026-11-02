import { describe, expect, it } from 'vitest';
import { bankPairs, rejectedKey } from './bank-pair';

describe('bankPairs', () => {
	it('pairs a sort code with the account number under the same prefix', () => {
		const pairs = bankPairs([
			{ fieldKey: 'bank_account.sort_code', fieldType: 'sortCode' },
			{ fieldKey: 'bank_account.account_name', fieldType: 'text' },
			{ fieldKey: 'bank_account.account_number', fieldType: 'bankAccountNumber' },
			{ fieldKey: 'bank_account.bank', fieldType: 'text' }
		]);
		expect(pairs).toEqual([
			{
				prefix: 'bank_account',
				sortKey: 'bank_account.sort_code',
				accountKey: 'bank_account.account_number',
				nameKey: 'bank_account.bank',
				anchorKey: 'bank_account.account_number'
			}
		]);
	});

	it('does not pair a sort code with an account number from another object', () => {
		expect(
			bankPairs([{ fieldKey: 'bank_account.sort_code' }, { fieldKey: 'pension.account_number' }])
		).toEqual([]);
	});
});

describe('rejectedKey', () => {
	const pair = bankPairs([
		{ fieldKey: 'bank_account.sort_code' },
		{ fieldKey: 'bank_account.account_number' }
	])[0]!;

	it('names the sort code when the directory does not know it', () => {
		expect(rejectedKey(pair, 'sort_code_unknown')).toBe('bank_account.sort_code');
	});

	it('names the account number for every other refusal', () => {
		expect(rejectedKey(pair, 'account_invalid')).toBe('bank_account.account_number');
	});
});
