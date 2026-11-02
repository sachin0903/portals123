/** The clients list's search / segment / sort predicates, and the honest header summary.
 *
 *  These live outside the component so they can be asserted directly, and the header summary is a
 *  money figure — it used to pair the WHOLE book's client count with the FILTERED rows' AUM, so
 *  applying any search made the page state a total that didn't belong to the population beside it.
 *
 *  Filtering is still client-side over the already-loaded array: `GET /crm/contacts` accepts no
 *  `q` / `segment` / `sort` / `limit`, so there is nothing to page against yet.
 */
import { clientName } from './format.js';
import { canonicalPhoneNumber } from '../../../forms/editor/masks.js';

/** Just the fields the list logic reads — structural, so it fits `PageData['clients'][number]`
 *  without this module depending on the loader's shape. */
export type BookListRow = {
	type: 'individual' | 'organisation';
	name: string | null;
	title: string | null;
	first_name: string | null;
	surname: string | null;
	email: string | null;
	mobile: string | null;
	phone?: string | null;
	uid: string;
	advisers: string[];
	enabled: boolean;
	service_type: 'advised' | 'execution_only' | null;
	account_count: number;
	valued_account_count: number;
	portfolio_value: number;
};

export const BOOK_SEGMENTS = ['all', 'advised', 'execution_only', 'active', 'inactive'] as const;
export type BookSegment = (typeof BOOK_SEGMENTS)[number];

export const BOOK_SORTS = ['aum', 'name', 'accounts'] as const;
export type BookSort = (typeof BOOK_SORTS)[number];

/** Whether a client belongs to a segment. "Active" is "holds an account that can carry value AND is
 *  enabled" — it says nothing about whether an onboarding journey is under way, because nothing
 *  here consults a flow's state. */
export function inBookSegment(c: BookListRow, segment: string): boolean {
	if (segment === 'advised') return c.service_type === 'advised';
	if (segment === 'execution_only') return c.service_type === 'execution_only';
	if (segment === 'active') return c.valued_account_count > 0 && c.enabled;
	if (segment === 'inactive') return !c.enabled;
	return true;
}

/** Substring search across the fields staff actually quote on a call — name, email, EVERY number
 *  the contact holds, the client reference (`uid`) and the adviser.
 *
 *  Every number, not just the primary mobile: a client rings from their work landline and the caller
 *  types what the display showed them, so a search that only knew the mobile answered "no such
 *  client" about somebody already on the line.
 *
 *  Numbers are matched on DIGITS ALONE at both ends, through the SAME `canonicalPhoneNumber` the
 *  phone fields write with. Stored is `+44 7700 900123`; typed is the six digits off a screen
 *  (`900123`), the number as the client says it (`07700 900123`), or a paste carrying its own
 *  spacing — a plain substring match over the stored text finds none of them, and stripping the
 *  punctuation alone still leaves `07700…` failing to match `+447700…`. Everything else stays a
 *  case-insensitive substring: only a phone number is written one way and read another.
 *
 *  Only a query that could BE a number is compared that way (digits and the punctuation a number
 *  is written with). Stripping the non-digits out of anything would make `abc123` — the tail of a
 *  client reference — a search for "123", which is in most phone numbers in the book. */
const PHONE_QUERY = /^[\d\s+()./-]+$/;

const phoneDigits = (value: string | null | undefined): string =>
	canonicalPhoneNumber(value ?? '').replace(/\D/g, '');

export function matchesClientQuery(c: BookListRow, query: string): boolean {
	const n = query.trim().toLowerCase();
	if (!n) return true;
	const digits = PHONE_QUERY.test(n) ? phoneDigits(n) : '';
	const numbers = [c.mobile, c.phone];
	return (
		clientName(c).toLowerCase().includes(n) ||
		(c.email ?? '').toLowerCase().includes(n) ||
		(digits !== '' && numbers.some((value) => phoneDigits(value).includes(digits))) ||
		(c.uid ?? '').toLowerCase().includes(n) ||
		c.advisers.some((a) => a.toLowerCase().includes(n))
	);
}

/** Filter + sort in one pass. Does not mutate the input. */
export function visibleClientRows<T extends BookListRow>(
	rows: T[],
	query: string,
	segment: string,
	sort: string
): T[] {
	return rows
		.filter((c) => inBookSegment(c, segment) && matchesClientQuery(c, query))
		.sort((a, b) => {
			if (sort === 'name') return clientName(a).localeCompare(clientName(b));
			if (sort === 'accounts') return b.account_count - a.account_count;
			return b.portfolio_value - a.portfolio_value;
		});
}

/** The page header line. The count and the money must describe the SAME population: when a filter
 *  is applied it says "12 of 240 clients" and the AUM is those 12's. */
export function bookSummary(
	shown: { portfolio_value: number }[],
	total: number,
	formatMoney: (n: number) => string,
	asAt: string | null
): string {
	const aum = shown.reduce((s, c) => s + c.portfolio_value, 0);
	const noun = shown.length === 1 ? 'client' : 'clients';
	const head =
		shown.length === total
			? `${total} ${noun}`
			: `${shown.length} of ${total} ${total === 1 ? 'client' : 'clients'}`;
	return `${head} · ${formatMoney(aum)} under advice${asAt ? ` · values as at ${asAt}` : ''}`;
}
