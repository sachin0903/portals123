/**
 * What the transaction drawer shows about one movement, as data.
 *
 * A pure builder rather than markup, for the reason the ledger's own column rules are pure: the
 * drawer is opened from four mounts (the firm ledger, a client's, an account's band and the admin
 * book's read-only copy) and the answer to "what does this row actually say" must not depend on
 * which one you came from. It also makes the rules testable without mounting anything — the
 * decision that matters here is what is LEFT OUT.
 */

import { humanise } from '../listing/filters.js';
import { custodianLabel } from '../listing/custodian.js';
import { providerReference, transactionReference } from './format.js';
import { formatDate } from '../../../dates.js';
import { formatMoney, formatUnitPrice } from '../../../money.js';
import {
	chargeBreakdown,
	entitlementLabel,
	subtypeDetail,
	type Transaction
} from './transactions.js';

export type TransactionDetailField = {
	label: string;
	value: string;
	/** Set on money fields so the drawer can split pounds from pence; `value` stays the text form. */
	money?: { amount: number; currency: string };
	/** An identifier, read character by character against a custodian's statement — never a word. */
	mono?: boolean;
	/** An ISO currency code, drawn with its flag as the ledger's Currency column draws it. */
	currency?: string;
	/** A colleague's name, drawn with their face as the ledger's adviser columns draw it. */
	person?: string;
};

function moneyField(label: string, amount: number, currency: string): TransactionDetailField {
	return { label, value: formatMoney(amount, currency), money: { amount, currency } };
}

export type TransactionDetailGroup = {
	title: string;
	fields: TransactionDetailField[];
};

/**
 * The drawer's field groups.
 *
 * Everything the row shows is here too, so opening a row never loses a fact the table had: the
 * asset ("Cash" when there is none, as the Asset column says), the account's id and the adviser
 * ("Unassigned" when there is none). Past those, a field with nothing in it is DROPPED, and a
 * group left with no fields goes with it — so a cash movement's panel carries no em-dashes for an
 * ISIN and a unit count it never had. The identity of the movement itself (reference, date,
 * type) is always stated: those are what tell one row from another.
 *
 * The amount and the status stay in the heading, as money and a badge. Everything the custodian
 * actually populated — and only that — follows in its own group, so a cash row does not grow a
 * column of dashes and a foreign receipt still shows the rate, the narrative and the party ids.
 */
export function transactionDetailGroups(t: Transaction): TransactionDetailGroup[] {
	const detail = subtypeDetail(t);
	const recorded = providerFields(t.record);
	const groups: TransactionDetailGroup[] = [
		{
			title: 'The movement',
			fields: [
				{ label: 'Reference', value: transactionReference(t.uid), mono: true },
				{ label: 'Date', value: formatDate(t.date) },
				...(t.settlement_date
					? [{ label: 'Settlement date', value: formatDate(t.settlement_date) }]
					: []),
				{ label: 'Type', value: humanise(t.type) },
				// The custodian's own word, only where it says more than the canonical type already
				// does — the same test the Type column applies, so the two cannot disagree.
				...(detail ? [{ label: 'Detail', value: detail }] : []),
				...(t.category ? [{ label: 'Category', value: humanise(t.category) }] : []),
				...(t.method ? [{ label: 'Method', value: humanise(t.method) }] : []),
				...(t.currency ? [{ label: 'Currency', value: t.currency, currency: t.currency }] : []),
				...(t.fee_amount != null ? [moneyField('Fee', t.fee_amount, t.currency ?? 'GBP')] : []),
				...chargeBreakdown(t).map((line) =>
					moneyField(line.label, line.amount, t.currency ?? 'GBP')
				),
				...(t.fee_type ? [{ label: 'Fee type', value: t.fee_type }] : []),
				...(t.trade_date ? [{ label: entitlementLabel(t), value: formatDate(t.trade_date) }] : []),
				...(t.report_group_kind ? [{ label: 'Statement group', value: t.report_group_kind }] : []),
				...(t.book_cost != null ? [moneyField('Book cost', t.book_cost, t.currency ?? 'GBP')] : []),
				...(t.realised_gain != null
					? [moneyField('Realised gain', t.realised_gain, t.currency ?? 'GBP')]
					: []),
				...(t.equalisation_payment != null
					? [moneyField('Equalisation', t.equalisation_payment, t.currency ?? 'GBP')]
					: []),
				...(t.subscription_amount != null
					? [moneyField('Subscription', t.subscription_amount, t.currency ?? 'GBP')]
					: []),
				...(t.tax_amount != null ? [moneyField('Tax', t.tax_amount, t.currency ?? 'GBP')] : []),
				...(showsSettlement(t)
					? [moneyField('Settlement', t.settlement_amount ?? 0, t.currency ?? 'GBP')]
					: []),
				...(t.custodian ? [{ label: 'Custodian', value: custodianLabel(t.custodian) }] : [])
			]
		},
		{
			title: 'The investment',
			fields: [
				{ label: 'Asset', value: t.asset ?? 'Cash' },
				...(t.isin ? [{ label: 'ISIN', value: t.isin, mono: true }] : []),
				...(t.quantity == null
					? []
					: [{ label: 'Units', value: t.quantity.toLocaleString('en-GB') }]),
				...(t.price != null
					? [{ label: 'Price', value: formatUnitPrice(t.price, t.currency ?? 'GBP') }]
					: [])
			]
		},
		{
			title: 'Where it sits',
			fields: [
				...(t.account_uid
					? [{ label: 'Account ID', value: providerReference(t.account_uid) ?? '', mono: true }]
					: []),
				t.adviser
					? { label: 'Adviser', value: t.adviser, person: t.adviser }
					: { label: 'Adviser', value: 'Unassigned' },
				...(t.product_type ? [{ label: 'Wrapper', value: humanise(t.product_type) }] : [])
			]
		},
		...(recorded.length ? [{ title: 'Recorded by the custodian', fields: recorded }] : [])
	];
	return groups.filter((group) => group.fields.length > 0);
}

/** These are the Price, Fee and Tax rows. The payload's own spelling of them is not a second copy. */
const FIGURE_KEYS = new Set([
	'unit_price',
	'net_fee_amt',
	'net_taxes_amt',
	'net_charges_amount',
	'net_taxes_amount',
	'fee_amount',
	'tax_amount'
]);

function showsSettlement(t: Transaction): boolean {
	if (t.settlement_amount == null || !Number.isFinite(t.settlement_amount)) return false;
	// A stock transfer has no cash. Its headline is the book value and the cash net is 0;
	// printing that as a £0.00 settlement reads as a movement that settled for nothing.
	if (t.settlement_amount === 0) return false;
	return Math.abs(t.settlement_amount - t.amount) >= 0.005;
}

const IDENTIFIER_TAIL = /^(id|code|isin|sedol|cusip|number|reference)$/i;
const DATE_PREFIX = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?)?/;

/**
 * One row per populated custodian field. Labels are sentence case; identifiers stay
 * ungrouped so an id is not read as a quantity.
 */
export function providerFields(
	record: Record<string, string> | null | undefined
): TransactionDetailField[] {
	if (!record) return [];
	return Object.entries(record)
		.filter(([key]) => !FIGURE_KEYS.has(key.toLowerCase()))
		.map(([key, raw]) => ({
			label: humanise(key.toLowerCase()),
			value: presentRecordValue(key, raw),
			...(isIdentifier(key) ? { mono: true } : {})
		}))
		.filter((field) => field.value !== '')
		.sort((a, b) => a.label.localeCompare(b.label, 'en-GB'));
}

function isIdentifier(key: string): boolean {
	const tail = key.split('_').at(-1) ?? key;
	return IDENTIFIER_TAIL.test(tail);
}

function presentRecordValue(key: string, raw: string): string {
	const text = raw.trim();
	const dated = DATE_PREFIX.exec(text);
	if (dated?.[1]) {
		const day = formatDate(dated[1]);
		if (day !== '—') {
			const time = dated[2];
			return time && time !== '00:00' ? `${day}, ${time}` : day;
		}
	}
	if (!isIdentifier(key) && /^-?\d+(\.\d+)?$/.test(text)) {
		const n = Number(text);
		if (Number.isFinite(n)) return n.toLocaleString('en-GB', { maximumFractionDigits: 8 });
	}
	return text;
}
