/** What the sort-code directory said about one UK account. Mirrors `POST /ref/postcoder/bank`. */

export type BankCheckStatus = 'idle' | 'checking' | 'confirmed' | 'rejected' | 'unavailable';

export type BankCheckSupports = {
	bacs: boolean;
	faster_payments: boolean;
	chaps: boolean;
	direct_debits: boolean;
};

export type BankCheckResult = {
	valid: boolean;
	state: 'valid' | 'sort_code_unknown' | 'account_invalid';
	sort_code: string;
	account_number: string;
	bank_name: string;
	branch_name: string;
	bank_bic: string;
	supports: BankCheckSupports;
	message: string;
};

export const BANK_CHECKING_MESSAGE = 'Checking these bank details…';
export const BANK_UNAVAILABLE_MESSAGE = "Couldn't check these details. You can still save them.";
export const BANK_CONFIRMED_NOTE = "Confirmed with the bank's sort code directory.";

export const BANK_RAILS: { key: keyof BankCheckSupports; label: string }[] = [
	{ key: 'bacs', label: 'BACS' },
	{ key: 'faster_payments', label: 'Faster Payments' },
	{ key: 'chaps', label: 'CHAPS' },
	{ key: 'direct_debits', label: 'Direct Debit' }
];

export function bankDigits(value: unknown): string {
	return String(value ?? '').replace(/\D/g, '');
}

export function bankPairComplete(sortCode: unknown, accountNumber: unknown): boolean {
	return bankDigits(sortCode).length === 6 && bankDigits(accountNumber).length === 8;
}
