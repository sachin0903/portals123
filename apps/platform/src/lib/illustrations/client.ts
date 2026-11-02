import { MARITAL_OPTIONS } from '@graphene/ui';

/** The client facts the illustration form reads and, for two of them, can write back. */
export type IllustrationClient = {
	type?: string | null;
	/** An organisation's kind, so the panel can name a trust as a trust. */
	sub_type?: string | null;
	dob?: string | null;
	retirement_age?: number | null;
	marital_status?: string | null;
};

export function maritalLabel(status: string | null | undefined): string | null {
	if (!status) return null;
	return MARITAL_OPTIONS.find((o) => o.value === status)?.label ?? status;
}

/** An account as far as the SIPP total needs it. The contact shell and the client record both
    carry `product_type` and `value`. */
export type SippAccountValue = {
	product_type?: string | null;
	value?: number | string | null;
};

/**
 * The current value CTC should print: the sum of this client's SIPP accounts.
 *
 * Null when they have none, so the panel can hide the line — a new client in kickoff sees
 * nothing there. Zero is a real total: they have a SIPP and it is worth nothing yet. Other
 * pensions are not included; this figure is the existing fund of a SIPP illustration.
 */
export function existingSippTotal(accounts: SippAccountValue[] | null | undefined): number | null {
	const sipps = (accounts ?? []).filter(
		(account) => (account.product_type ?? '').toUpperCase() === 'SIPP'
	);
	if (!sipps.length) return null;
	return sipps.reduce((sum, account) => {
		const value = Number(account.value);
		return sum + (Number.isFinite(value) ? value : 0);
	}, 0);
}
