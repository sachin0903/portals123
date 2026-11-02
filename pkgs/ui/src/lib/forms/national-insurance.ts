/**
 * UK National Insurance numbers, as HMRC issues them: two letters, six digits and a suffix
 * letter A–D. Not every letter pair is a prefix — D, F, I, Q, U and V are never used in either
 * position, O is never the second letter, and BG, GB, KN, NK, NT, TN and ZZ are not allocated.
 * The one exception is `QQ`: HMRC's own placeholder prefix (`QQ 12 34 56 C` in its guidance),
 * which is accepted so that example and test data pass. The same rules are applied server-side
 * (`apps/api/app/national_insurance.py`).
 */

/** Source for a `RegExp` / form-field `pattern`: accepts the spaced or compact form, any case. */
export const NATIONAL_INSURANCE_PATTERN =
	'^(?:[Qq][Qq]|(?![DFIQUVdfiquv])(?![A-Za-z][DFIOQUVdfioquv])(?![Bb][Gg]|[Gg][Bb]|[Kk][Nn]|[Nn][Kk]|[Nn][Tt]|[Tt][Nn]|[Zz][Zz])[A-Za-z]{2}) ?\\d{2} ?\\d{2} ?\\d{2} ?[A-Da-d]$';

export const NATIONAL_INSURANCE_MESSAGE =
	'Enter a valid National Insurance number, for example QQ 12 34 56 C';

const PATTERN = new RegExp(NATIONAL_INSURANCE_PATTERN);

/** How a UK National Insurance number is named. The fact find stores the regulator's words. */
const NINO_TYPES = new Set(['nino', 'national_insurance_number', 'national insurance number']);

/** Strip spaces and hyphens and upper-case — the stored form (`QQ123456C`). */
export function normaliseNationalInsuranceNumber(raw: string): string {
	return raw.replace(/[\s-]/g, '').toUpperCase();
}

export function isValidNationalInsuranceNumber(raw: string): boolean {
	return PATTERN.test(normaliseNationalInsuranceNumber(raw));
}

export function isNinoType(value: unknown): boolean {
	return NINO_TYPES.has(
		String(value ?? '')
			.trim()
			.toLowerCase()
	);
}

/** A present National Insurance number that HMRC would not issue, or null when there is nothing to say. */
export function nationalInsuranceError(type: unknown, value: unknown): string | null {
	if (!isNinoType(type)) return null;
	if (value == null || String(value).trim() === '') return null;
	return isValidNationalInsuranceNumber(String(value)) ? null : NATIONAL_INSURANCE_MESSAGE;
}

/** Display form with HMRC's pairs: `QQ 12 34 56 C`. Values that do not validate pass through. */
export function formatNationalInsuranceNumber(raw: string): string {
	const compact = normaliseNationalInsuranceNumber(raw);
	if (!PATTERN.test(compact)) return raw;
	return `${compact.slice(0, 2)} ${compact.slice(2, 4)} ${compact.slice(4, 6)} ${compact.slice(6, 8)} ${compact.slice(8)}`;
}
