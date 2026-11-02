/** A two-letter country code as its English name ("BY" → "Belarus"); the code itself when it is
 *  not one, and an empty string for none. */
export function countryName(code: string | null | undefined): string {
	if (!code) return '';
	try {
		return new Intl.DisplayNames('en-GB', { type: 'region' }).of(code.toUpperCase()) ?? code;
	} catch {
		return code;
	}
}
