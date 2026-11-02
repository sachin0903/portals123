import { describe, expect, it, vi } from 'vitest';
import {
	canonicalPhoneNumber,
	canonicalSortCode,
	emailIssue,
	formatBankAccountNumber,
	formatPhoneNumber,
	formatSortCode,
	phoneIssue,
	rejectNonDigitInsert,
	rejectNonPhoneInsert,
	sanitisePhoneInput
} from './masks.js';

describe('phone number mask', () => {
	it('groups a UK mobile as country code, four-digit network and six-digit subscriber', () => {
		expect(formatPhoneNumber('07700900123')).toBe('+44 7700 900123');
		expect(formatPhoneNumber('+447700900123')).toBe('+44 7700 900123');
	});

	it('uses recognisable UK geographic groupings', () => {
		expect(formatPhoneNumber('02079460958')).toBe('+44 20 7946 0958');
		expect(formatPhoneNumber('01134960000')).toBe('+44 113 496 0000');
		expect(formatPhoneNumber('01234567890')).toBe('+44 1234 567890');
	});

	it('stores the same compact E.164 value regardless of display spacing', () => {
		expect(canonicalPhoneNumber('+44 7700 900123')).toBe('+447700900123');
	});

	it('standardises a UK trunk number, and the same number dialled as 0044 or +440, to +44', () => {
		expect(formatPhoneNumber('07812345678')).toBe('+44 7812 345678');
		expect(canonicalPhoneNumber('07812 345678')).toBe('+447812345678');
		expect(canonicalPhoneNumber('00447812345678')).toBe('+447812345678');
		expect(canonicalPhoneNumber('+4407812345678')).toBe('+447812345678');
		expect(formatPhoneNumber('02079460958')).toBe('+44 20 7946 0958');
	});
});

describe('sort code mask', () => {
	it('groups six digits the way a sort code is read', () => {
		expect(formatSortCode('123456')).toBe('12 - 34 - 56');
		expect(formatSortCode('12-34-56')).toBe('12 - 34 - 56');
		expect(formatSortCode('12 34 56')).toBe('12 - 34 - 56');
	});

	it('stores compact hyphenated form when complete, digits while typing', () => {
		expect(canonicalSortCode('12 - 34 - 56')).toBe('12-34-56');
		expect(canonicalSortCode('1234')).toBe('1234');
	});
});

describe('bank account number mask', () => {
	it('keeps eight digits and drops everything else', () => {
		expect(formatBankAccountNumber('12 34 56 78')).toBe('12345678');
		expect(formatBankAccountNumber('1234567890')).toBe('12345678');
		expect(formatBankAccountNumber('12ab5678')).toBe('125678');
	});
});

describe('rejectNonDigitInsert', () => {
	it('prevents a letter keystroke and lets a digit through', () => {
		const letter = { inputType: 'insertText', data: 'a', preventDefault: vi.fn() };
		rejectNonDigitInsert(letter);
		expect(letter.preventDefault).toHaveBeenCalled();
		const digit = { inputType: 'insertText', data: '4', preventDefault: vi.fn() };
		rejectNonDigitInsert(digit);
		expect(digit.preventDefault).not.toHaveBeenCalled();
	});

	it('lets a paste through so a copied sort code can be formatted', () => {
		const paste = { inputType: 'insertFromPaste', data: '12-34-56', preventDefault: vi.fn() };
		rejectNonDigitInsert(paste);
		expect(paste.preventDefault).not.toHaveBeenCalled();
	});
});

describe('emailIssue', () => {
	it('accepts an address the server actions accept', () => {
		for (const ok of ['theo@caldwell.test', 'a.b+c@example.co.uk', '  theo@caldwell.test  ']) {
			expect(emailIssue(ok)).toBeNull();
		}
	});

	it('names an address the server actions would refuse, as it is typed', () => {
		// The same shape as `EMAIL_RE` in the clients routes, so the box and the action agree.
		for (const bad of [
			'theo',
			'theo@',
			'theo@caldwell',
			'theo@caldwell.t',
			'theo @caldwell.test'
		]) {
			expect(emailIssue(bad)).toContain('Enter an email address');
		}
	});

	it('says nothing about an empty field, which is a legitimate clear', () => {
		expect(emailIssue('')).toBeNull();
		expect(emailIssue('   ')).toBeNull();
	});
});

describe('phoneIssue', () => {
	it('accepts a UK number however it was typed', () => {
		// The three spellings a person actually uses. `canonicalPhoneNumber` turns the leading 0 into
		// +44, so all three are the same number by the time the rule sees them.
		for (const ok of ['07700 900123', '+44 7700 900123', '01234567890']) {
			expect(phoneIssue(ok)).toBeNull();
		}
	});

	it('refuses a UK number that is too short, and says what is expected', () => {
		// The reported defect: nine digits saved happily and only failed when somebody rang it.
		expect(phoneIssue('077009001')).toContain('11 digits');
		expect(phoneIssue('+44 7700 9001')).toContain('11 digits');
	});

	it('leaves an overseas number to E.164 rather than asserting a length it cannot know', () => {
		expect(phoneIssue('+353 1 234 5678')).toBeNull();
		expect(phoneIssue('+1 415 555 2671')).toBeNull();
		// Below E.164's own floor is still wrong, whoever's plan it is.
		expect(phoneIssue('+353 12')).toContain('country code');
	});

	it('says nothing about an empty field, which is a legitimate clear', () => {
		expect(phoneIssue('')).toBeNull();
		expect(phoneIssue('   ')).toBeNull();
	});
});

describe('phoneIssue: a number has to be identifiably one thing', () => {
	it('refuses bare digits that are neither UK nor international', () => {
		// What the field actually accepted before: seven ones, no `0`, no `+`. It cleared E.164's
		// floor as a "foreign" number and read back as "+11 1111 1".
		expect(phoneIssue('1111111')).toContain('country code');
		expect(phoneIssue('7700900123')).toContain('country code');
	});

	it('still takes a UK number typed the way people type it', () => {
		expect(phoneIssue('07700 900123')).toBeNull();
		expect(phoneIssue('+447700900123')).toBeNull();
	});

	it('refuses letters rather than silently dropping them', () => {
		expect(phoneIssue('0770 CALL ME')).toContain('digits only');
	});
});

describe('typing a phone number', () => {
	const evt = (data: string | null, inputType = 'insertText') => {
		let prevented = false;
		rejectNonPhoneInsert({ inputType, data, preventDefault: () => (prevented = true) });
		return prevented;
	};

	it('refuses a letter as it is typed, but takes + and the separators', () => {
		expect(evt('q')).toBe(true);
		expect(evt('+')).toBe(false);
		expect(evt('7')).toBe(false);
		expect(evt(' ')).toBe(false);
	});

	it('leaves deletions and non-text input alone', () => {
		expect(evt(null, 'deleteContentBackward')).toBe(false);
	});

	it('cleans a paste, which beforeinput cannot filter character by character', () => {
		expect(sanitisePhoneInput('+1111111qasdghd433333')).toBe('+1111111433333');
		expect(sanitisePhoneInput('07700 900123')).toBe('07700 900123');
	});

	it('stops at E.164’s 15-digit ceiling, so an essay cannot sit in the box', () => {
		const long = sanitisePhoneInput('+' + '3'.repeat(40));
		expect(long.replace(/\D/g, '')).toHaveLength(15);
	});
});
