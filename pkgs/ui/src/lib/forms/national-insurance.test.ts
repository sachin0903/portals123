import { describe, expect, it } from 'vitest';
import {
	isNinoType,
	isValidNationalInsuranceNumber,
	nationalInsuranceError,
	normaliseNationalInsuranceNumber
} from './national-insurance';

describe('national insurance numbers', () => {
	it('stores a valid number compact', () => {
		expect(normaliseNationalInsuranceNumber('qq 12 34 56 c')).toBe('QQ123456C');
		expect(isValidNationalInsuranceNumber('QQ123456C')).toBe(true);
	});

	it('refuses a prefix HMRC does not issue', () => {
		expect(isValidNationalInsuranceNumber('BG123456A')).toBe(false);
		expect(nationalInsuranceError('National Insurance Number', 'BG123456A')).toMatch(
			/National Insurance/
		);
	});

	it('leaves a passport alone', () => {
		expect(isNinoType('Passport')).toBe(false);
		expect(nationalInsuranceError('Passport', 'BG123456A')).toBeNull();
		expect(nationalInsuranceError('nino', '')).toBeNull();
	});
});
