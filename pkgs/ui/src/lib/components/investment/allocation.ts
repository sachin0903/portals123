/**
 * A holding's ALLOCATION — its share of the whole it belongs to.
 *
 * Its own module because the base is the part that goes wrong. A holdings table is routinely
 * handed a SLICE (the account record pages 25 at a time; the client portal's holdings widget caps
 * at the top five), and a share computed over the rows in hand would say "42% of your portfolio"
 * about 42% of one page — a wrong number that reads exactly like a right one. So the base is
 * always supplied by whoever knows the real total, and `null` means the column is simply not
 * shown.
 */

/**
 * `value / base` as a fraction, or `null` when the share cannot be stated honestly.
 *
 * Null for a base of zero (an account valued at nothing has no shares of anything, and dividing
 * gives Infinity), for a non-finite input, and for a negative base — a share of a negative whole
 * is not a proportion. A NEGATIVE value over a positive base is kept: a short or an overdrawn
 * cash line is a real position, and its weight is really negative.
 */
export function allocationShare(value: number, base: number | null | undefined): number | null {
	if (base == null || !Number.isFinite(base) || base <= 0) return null;
	if (!Number.isFinite(value)) return null;
	return value / base;
}

/** The total to divide by, summed over the FULL set — what a caller passes as the base. */
export function allocationBaseOf(rows: { value: number }[]): number {
	return rows.reduce((total, row) => total + (Number.isFinite(row.value) ? row.value : 0), 0);
}
