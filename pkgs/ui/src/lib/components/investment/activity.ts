import { sentenceCase } from '../../sentence-case.js';
import { britishDate } from './provenance.js';

/**
 * How a movement is NAMED on a client's activity feed — the one place the canonical kinds are
 * turned into words.
 *
 * It is one place because it was two: `activity-list.svelte` and `activity-table.svelte` each
 * carried a copy, and a look that grew a third would have been the point at which the portal
 * started calling the same movement by two names on two pages.
 *
 * TOTAL over `platform.transaction_type`, the conformed enum every provider's rows are written
 * in. It was a six-kind subset with 'buy' as the fallback, so an interest payment printed as
 * "+£0.01 Buy" against a client's cash. The API's `_ACTIVITY_LABELS` (apps/api
 * `routers/crm.py`) is the same map in Python; a pin test reads this file to keep them equal.
 *
 * The vocabulary is the ADVISER platform's, deliberately: "Payment", the word its account
 * record puts on the button that raises one (`book/accounts/instruction.ts`
 * CASH_INSTRUCTION_TITLE). A client reading "deposit" while their adviser says "payment" is two
 * firms as far as the client can tell. It is "Payment" rather than "Payment in" for the reason
 * recorded beside that button — the money's direction is already what the word says.
 *
 * Nouns, not verbs: these name a thing that happened, where a BUTTON names the act ("Withdraw").
 */
export const ACTIVITY_LABELS: Record<string, string> = {
	deposit: 'Payment',
	withdrawal: 'Withdrawal',
	dividend: 'Dividend',
	interest: 'Interest',
	fee: 'Fee',
	tax: 'Tax',
	buy: 'Buy',
	sell: 'Sell',
	transfer_in: 'Transfer in',
	transfer_out: 'Transfer out',
	corporate_action: 'Corporate action',
	adjustment: 'Adjustment'
};

/**
 * One movement kind, as words.
 *
 * The fallback is `sentenceCase`, never the raw key: `type` is a closed bucket here, but the
 * same rule has to hold for the one caller that reaches this with a custodian's own word, and
 * a slug printed as a slug is what put "transfer_in" and "deposit" in front of clients
 * (docs/design-language.md — a value is named where it is produced, and `class="capitalize"`
 * is banned for being TITLE case).
 */
export function activityLabel(type: string): string {
	return ACTIVITY_LABELS[type] ?? sentenceCase(type);
}

/**
 * The units and price of a trade, as one phrase — `416 units @ £1.2345`; just the units when
 * the custodian sent no price. Null for a cash movement, which has neither, so a wide feed
 * leaves the cell blank rather than printing a dash down the whole column.
 *
 * Units keep up to four decimals and drop trailing zeros (a fund holding is fractional, a
 * share count is whole); the price keeps the pence-and-beyond the custodian priced at, to
 * four places, because a unit price is quoted finer than a sum of money.
 */
export function activityUnits(
	quantity: number | null | undefined,
	price: number | null | undefined,
	currency: string | null | undefined = 'GBP'
): string | null {
	if (quantity == null || !Number.isFinite(quantity) || quantity === 0) return null;
	const units = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 4 }).format(
		Math.abs(quantity)
	);
	if (price == null || !Number.isFinite(price) || price <= 0) return `${units} units`;
	const at = new Intl.NumberFormat('en-GB', {
		style: 'currency',
		currency: currency || 'GBP',
		minimumFractionDigits: 2,
		maximumFractionDigits: 4
	}).format(price);
	return `${units} units @ ${at}`;
}

/**
 * How a movement's DATE is printed on the feed — British display form, never the wire string.
 *
 * `Activity.date` arrives two ways: the API's `str(row["date"])` (a date-only `2025-09-10`, or
 * `2025-09-10T00:00:00` when the source column is a timestamp), and the demo fixture's
 * already-printed `'14 Jun 2026'`. The three looks (list, timeline, table) each printed the
 * string as it came, so a real book's feed showed `2025-09-10T00:00:00` as a group heading. An
 * ISO-looking value is formatted here (date-only, UTC — the same rule as the provenance stamps);
 * anything else is already words and passes through.
 *
 * Only the DATE part is parsed. A bare `T00:00:00` with no zone is local time to `Date.parse`,
 * and formatting that in UTC prints the previous day for every zone east of Greenwich — BST
 * included, so a 10 September movement read "9 Sept" in Britain in summer.
 */
export function activityDate(raw: string | null | undefined): string {
	if (!raw) return '';
	return britishDate(raw.slice(0, 10)) || raw;
}
