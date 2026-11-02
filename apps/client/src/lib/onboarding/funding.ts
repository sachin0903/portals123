/** Pension and JISA keep the word "contribution". Every other wrapper says "payment in". */
const CONTRIBUTION_PRODUCTS = new Set(['SIPP', 'JISA', 'TPPENSION']);

export function paymentInWording(productType: string | null | undefined): {
	row: string;
	fallback: string;
} {
	if (CONTRIBUTION_PRODUCTS.has((productType ?? '').trim().toUpperCase())) {
		return { row: 'Contributions', fallback: 'Contribution' };
	}
	return { row: 'Payments in', fallback: 'Payment in' };
}
