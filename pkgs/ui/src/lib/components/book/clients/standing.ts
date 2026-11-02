/**
 * A client's STANDING arrangements — the regular payments in and withdrawals out, across every
 * account they hold.
 *
 * **Why this is a client-level panel and not an account-level one.** It is both, and the account
 * record already has its own; but a regular payment is something the CLIENT set up. They think of it
 * as "my £100 a month", not as a property of one of their wrappers, and an adviser asked "what is
 * going in and out for this person?" should not have to open four accounts to answer. So the record
 * flattens them into one table and names the account on each row.
 *
 * The rows come from `platform.accounts.{payments,withdrawals}` — jsonb lists that the provider's
 * conformed layer (`derived/account_terms.sql` for Seccl), onboarding and the account record all
 * write. Older onboarding rows hold an object instead; the API turns those into lists.
 */

/** One standing arrangement, as the provider's conformed layer publishes it. */
export type StandingMovement = {
	amount?: number | null;
	/** The day of the month it runs on. Seccl states no frequency because a `paymentDay` IS one. */
	payment_day?: number | null;
	method?: string | null;
	frequency?: string | null;
	/**
	 * The account's rule for RAISING the cash a withdrawal needs ('Largest' sells down the biggest
	 * holding). Carried only on money going OUT — it is meaningless on an inbound payment, and the
	 * provider layer leaves it null there rather than repeating a rule that does not apply.
	 */
	auto_disinvest?: string | null;
};

/** The account a row belongs to. Structural, so both portals' loader types satisfy it. */
export type StandingAccount = {
	uid: string;
	name?: string | null;
	payments?: StandingMovement[] | null;
	withdrawals?: StandingMovement[] | null;
};

/** One row of the panel: a movement, plus which account it runs on and which way it goes. */
export type StandingRow = StandingMovement & {
	/** A stable key for the table. Composed rather than provider-supplied: Seccl's recurring payments
	 *  are an unkeyed array, so there is no id to use and position within the account is the identity. */
	key: string;
	direction: 'in' | 'out';
	accountUid: string;
	accountName: string;
};

/**
 * Every standing arrangement across a client's accounts, in the order a reader scans them.
 *
 * WITHDRAWALS FIRST, then by account, then by the day of the month. Money leaving is what an adviser
 * is checking (a withdrawal that will not be covered is the thing that goes wrong), and it is what
 * the client asks about — so it is not sorted alphabetically by a label nobody reads.
 */
export function standingRows(accounts: readonly StandingAccount[]): StandingRow[] {
	const rows: StandingRow[] = [];
	for (const account of accounts) {
		const name = account.name ?? account.uid;
		for (const [direction, list] of [
			['out', account.withdrawals],
			['in', account.payments]
		] as const) {
			// jsonb, so the type is a promise the column cannot keep: onboarding stored an object here.
			// A value that is not a list adds no rows rather than stopping the whole record.
			(Array.isArray(list) ? list : []).forEach((movement, index) => {
				rows.push({
					...movement,
					key: `${account.uid}:${direction}:${index}`,
					direction,
					accountUid: account.uid,
					accountName: name
				});
			});
		}
	}
	return rows.sort(
		(a, b) =>
			(a.direction === b.direction ? 0 : a.direction === 'out' ? -1 : 1) ||
			a.accountName.localeCompare(b.accountName, 'en-GB') ||
			(a.payment_day ?? 0) - (b.payment_day ?? 0)
	);
}

/**
 * "14th of every month".
 *
 * The two halves come from different places and neither is sufficient alone: the DAY is the
 * provider's (`paymentDay`) and the CADENCE is the conformed layer's, which states Monthly because a
 * `paymentDay` is monthly by construction. Rendered as one sentence here so the panel does not have
 * to assemble it, and so "the 1st" reads correctly rather than as "the 1th".
 */
export function scheduleLabel(
	movement: Pick<StandingMovement, 'payment_day' | 'frequency'>
): string {
	const cadence = (movement.frequency ?? 'Monthly').toLowerCase();
	const day = movement.payment_day;
	if (day === null || day === undefined) {
		// A schedule with no day is still a schedule — the provider simply did not say when.
		return cadence.charAt(0).toUpperCase() + cadence.slice(1);
	}
	return `${ordinal(day)} of every ${cadence === 'monthly' ? 'month' : cadence}`;
}

/** 1st, 2nd, 3rd, 4th… 11th, 12th, 13th, 21st. The teens are the exception every naive version
 *  gets wrong, which on a payment schedule is a date a client reads and does not recognise. */
export function ordinal(day: number): string {
	const tens = day % 100;
	if (tens >= 11 && tens <= 13) return `${day}th`;
	return `${day}${({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[day % 10] ?? 'th'}`;
}

/**
 * What the Auto-invest column shows.
 *
 * "N/A" on money coming IN, and that is the honest answer rather than a blank: the rule is about
 * raising cash to pay a withdrawal, so it does not apply to an inbound payment — where a blank cell
 * would read as "we do not know".
 */
export function autoInvestLabel(row: Pick<StandingRow, 'direction' | 'auto_disinvest'>): string {
	if (row.direction === 'in') return 'N/A';
	return row.auto_disinvest ?? 'Not set';
}
