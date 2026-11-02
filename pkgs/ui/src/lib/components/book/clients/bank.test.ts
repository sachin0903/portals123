import { describe, expect, it } from 'vitest';
import {
	bankDirectoryError,
	bankDisplayRows,
	bankMethodLabel,
	bankSchemeOf,
	formatIban,
	readBankDetails,
	validateBank
} from './bank';

const bacs = (over: Partial<Record<string, string>> = {}) => ({
	scheme: 'bacs' as const,
	name: 'T Caldwell',
	bank: '',
	sort_code: '12-34-56',
	account_number: '12345678',
	iban: '',
	bic: '',
	...over
});

const iban = (over: Partial<Record<string, string>> = {}) => ({
	scheme: 'iban' as const,
	name: 'T Caldwell',
	bank: '',
	sort_code: '',
	account_number: '',
	iban: 'GB82 WEST 1234 5698 7654 32',
	bic: 'BARCGB22',
	...over
});

describe('validateBank', () => {
	it('canonicalises whatever separators the adviser types', () => {
		for (const keyed of ['12-34-56', '123456', '12 34 56', '12/34/56']) {
			const out = validateBank(bacs({ sort_code: keyed }));
			expect(out.ok && out.value?.sort_code).toBe('12-34-56');
		}
	});

	it('pads a seven-digit account number, which is the Bacs convention', () => {
		const out = validateBank(bacs({ account_number: '1234567' }));
		expect(out.ok && out.value?.account_number).toBe('01234567');
	});

	it('omits the optional bank name rather than storing a blank', () => {
		const out = validateBank(bacs());
		expect(out.ok && out.value).toEqual({
			name: 'T Caldwell',
			sort_code: '12-34-56',
			account_number: '12345678'
		});
	});

	it('treats a wholly blank UK form as a request to clear', () => {
		const out = validateBank(bacs({ name: ' ', bank: '', sort_code: '', account_number: '  ' }));
		expect(out).toEqual({ ok: true, value: null });
	});

	it.each([
		['12345', '12345678', 'sort_code'],
		['1234567', '12345678', 'sort_code'],
		['12-34-5X', '12345678', 'sort_code'],
		['123456', '123456', 'account_number'],
		['123456', '1234567890', 'account_number']
	])('refuses sort %s / account %s on %s', (sort, account, field) => {
		const out = validateBank(bacs({ sort_code: sort, account_number: account }));
		expect(out.ok).toBe(false);
		expect(!out.ok && out.field).toBe(field);
	});

	it('requires the account holder once any UK detail is given', () => {
		const out = validateBank(bacs({ name: '' }));
		expect(out.ok).toBe(false);
		expect(!out.ok && out.field).toBe('name');
	});

	it.each(['T3 Caldwell', 'T Caldwell!', 'Caldwell & Sons'])(
		'refuses an account holder name with digits or symbols: %s',
		(name) => {
			const out = validateBank(bacs({ name }));
			expect(out.ok).toBe(false);
			expect(!out.ok && out.field).toBe('name');
		}
	);

	it.each(["O'Brien", 'Anne-Marie Smith', 'François Müller'])(
		'accepts an accented, hyphenated or apostrophised name: %s',
		(name) => {
			const out = validateBank(bacs({ name }));
			expect(out.ok && out.value?.name).toBe(name);
		}
	);

	it('never echoes the keyed digits in a message', () => {
		const out = validateBank(bacs({ sort_code: '99887' }));
		expect(!out.ok && out.error).not.toContain('99887');
	});

	it('stores an international account as compact IBAN + BIC', () => {
		const out = validateBank(iban());
		expect(out.ok && out.value).toEqual({
			name: 'T Caldwell',
			iban: 'GB82WEST12345698765432',
			bic: 'BARCGB22'
		});
	});

	it('treats a wholly blank international form as a request to clear', () => {
		expect(validateBank(iban({ name: '', iban: '', bic: '' }))).toEqual({
			ok: true,
			value: null
		});
	});

	it('refuses a checksum-invalid IBAN without echoing it', () => {
		const invalidIban = 'GB00WEST12345698765432';
		const out = validateBank(iban({ iban: invalidIban }));
		expect(out.ok).toBe(false);
		expect(!out.ok && out.field).toBe('iban');
		expect(!out.ok && out.error).not.toContain(invalidIban);
	});

	it('refuses a short BIC without echoing it', () => {
		const out = validateBank(iban({ bic: 'BARC' }));
		expect(out.ok).toBe(false);
		expect(!out.ok && out.field).toBe('bic');
		expect(!out.ok && out.error).not.toContain('BARC');
	});

	it('names IBAN when the international rail is selected, not the sort code', () => {
		const out = validateBank(iban({ iban: '', bic: 'BARCGB22' }));
		expect(!out.ok && out.field).toBe('iban');
	});

	it('refuses an international account holder name with digits or symbols too', () => {
		const out = validateBank(iban({ name: 'T3 Caldwell' }));
		expect(out.ok).toBe(false);
		expect(!out.ok && out.field).toBe('name');
	});

	it('stores Faster Payment when chosen', () => {
		const out = validateBank({ ...bacs(), method: 'faster_payment' });
		expect(out.ok && out.value).toEqual({
			name: 'T Caldwell',
			sort_code: '12-34-56',
			account_number: '12345678',
			method: 'faster_payment'
		});
	});

	it('omits method for BACS even when explicitly chosen — BACS is the default and is not written', () => {
		const out = validateBank({ ...bacs(), method: 'bacs' });
		expect(out.ok && out.value).toEqual({
			name: 'T Caldwell',
			sort_code: '12-34-56',
			account_number: '12345678'
		});
	});

	it('has no method to choose on the international rail — it is always SWIFT', () => {
		const out = validateBank({ ...iban(), method: 'faster_payment' });
		expect(out.ok && out.value).toEqual({
			name: 'T Caldwell',
			iban: 'GB82WEST12345698765432',
			bic: 'BARCGB22'
		});
	});
});

describe('formatIban', () => {
	it('groups a compact IBAN in fours', () => {
		expect(formatIban('GB82WEST12345698765432')).toBe('GB82 WEST 1234 5698 7654 32');
	});
});

describe('bankSchemeOf', () => {
	it('reads an IBAN row as international even without a stored scheme', () => {
		expect(bankSchemeOf({ name: 'A', iban: 'GB82WEST12345698765432', bic: 'BARCGB22' })).toBe(
			'iban'
		);
		expect(bankSchemeOf({ name: 'A', sort_code: '12-34-56', account_number: '12345678' })).toBe(
			'bacs'
		);
		expect(bankSchemeOf(null)).toBe('bacs');
	});
});

describe('bankDisplayRows', () => {
	it('shows IBAN grouped, and skips Bank on a UK row that has none', () => {
		expect(
			bankDisplayRows({
				name: 'A',
				iban: 'GB82WEST12345698765432',
				bic: 'BARCGB22'
			})
		).toEqual([
			['Account name', 'A'],
			['IBAN', 'GB82 WEST 1234 5698 7654 32'],
			['SWIFT/BIC', 'BARCGB22']
		]);
		expect(
			bankDisplayRows({ name: 'A', sort_code: '12-34-56', account_number: '12345678' })
		).toEqual([
			['Account name', 'A'],
			['Sort code', '12 - 34 - 56'],
			['Account number', '12345678'],
			['Payment method', 'BACS']
		]);
		expect(
			bankDisplayRows({ name: 'A', sort_code: '123456', account_number: '12 345 678' })
		).toEqual([
			['Account name', 'A'],
			['Sort code', '12 - 34 - 56'],
			['Account number', '12345678'],
			['Payment method', 'BACS']
		]);
	});

	it('states Faster Payment on the UK rail when that is what is stored', () => {
		expect(
			bankDisplayRows({
				name: 'A',
				sort_code: '12-34-56',
				account_number: '12345678',
				method: 'faster_payment'
			})
		).toEqual([
			['Account name', 'A'],
			['Sort code', '12 - 34 - 56'],
			['Account number', '12345678'],
			['Payment method', 'Faster Payment']
		]);
	});

	it('leads with the bank name when one is stored', () => {
		expect(
			bankDisplayRows({
				name: 'A',
				bank: 'Monzo',
				sort_code: '12-34-56',
				account_number: '12345678'
			})
		).toEqual([
			['Bank', 'Monzo'],
			['Account name', 'A'],
			['Sort code', '12 - 34 - 56'],
			['Account number', '12345678'],
			['Payment method', 'BACS']
		]);
	});

	it('has no Payment method row on the international rail — it is always SWIFT', () => {
		const rows = bankDisplayRows({ name: 'A', iban: 'GB82WEST12345698765432', bic: 'BARCGB22' });
		expect(rows.map((row) => row[0])).not.toContain('Payment method');
	});
});

describe('bankMethodLabel', () => {
	it('reads an absent method as BACS and names wider account-level rails honestly', () => {
		expect(bankMethodLabel(null)).toBe('BACS');
		expect(bankMethodLabel(undefined)).toBe('BACS');
		expect(bankMethodLabel('bacs')).toBe('BACS');
		expect(bankMethodLabel('cheque')).toBe('Cheque');
		expect(bankMethodLabel('chaps')).toBe('CHAPS');
	});

	it('reads Faster Payment', () => {
		expect(bankMethodLabel('faster_payment')).toBe('Faster Payment');
	});
});

describe('readBankDetails', () => {
	it('narrows the free-form jsonb rather than trusting it', () => {
		expect(readBankDetails(null)).toBeNull();
		expect(readBankDetails({})).toBeNull();
		expect(readBankDetails({ bank_account: null })).toBeNull();
		expect(readBankDetails({ bank_account: 'nope' })).toBeNull();
		// Every field blank/absent reads as absent, so the record shows an empty state rather
		// than four dashes over a row that technically exists.
		expect(readBankDetails({ bank_account: { name: '  ', sort_code: '' } })).toBeNull();
	});

	it('keeps a numeric sort code that a hand-written row stored unquoted', () => {
		expect(readBankDetails({ bank_account: { name: 'A B', sort_code: 123456 } })).toEqual({
			name: 'A B',
			bank: null,
			sort_code: '123456',
			account_number: null,
			iban: null,
			bic: null,
			method: null
		});
	});

	it('reads an international row', () => {
		expect(
			readBankDetails({
				bank_account: { name: 'A B', iban: 'GB82WEST12345698765432', bic: 'BARCGB22' }
			})
		).toEqual({
			name: 'A B',
			bank: null,
			sort_code: null,
			account_number: null,
			iban: 'GB82WEST12345698765432',
			bic: 'BARCGB22',
			method: null
		});
	});

	it('reads a stored Faster Payment choice and preserves wider provider-authored rails', () => {
		expect(
			readBankDetails({
				bank_account: {
					name: 'A B',
					sort_code: '123456',
					account_number: '12345678',
					method: 'faster_payment'
				}
			})?.method
		).toBe('faster_payment');
		expect(
			readBankDetails({
				bank_account: {
					name: 'A B',
					sort_code: '123456',
					account_number: '12345678',
					method: 'cheque'
				}
			})?.method
		).toBe('cheque');
	});
});

describe('bankDirectoryError', () => {
	it('blocks a save that is still being checked or that the directory refused', () => {
		expect(bankDirectoryError('bacs', 'checking', null, null)).toEqual({
			field: 'account_number',
			error: 'Checking these bank details…'
		});
		expect(
			bankDirectoryError(
				'bacs',
				'rejected',
				'sort_code_unknown',
				"This sort code isn't recognised."
			)
		).toEqual({ field: 'sort_code', error: "This sort code isn't recognised." });
		expect(bankDirectoryError('bacs', 'rejected', 'account_invalid', null)?.field).toBe(
			'account_number'
		);
	});

	it('lets an international save and an unreachable directory through', () => {
		expect(bankDirectoryError('iban', 'rejected', 'account_invalid', 'no')).toBeNull();
		expect(bankDirectoryError('bacs', 'unavailable', null, null)).toBeNull();
		expect(bankDirectoryError('bacs', 'confirmed', 'valid', '')).toBeNull();
		expect(bankDirectoryError('bacs', 'idle', null, null)).toBeNull();
	});
});
