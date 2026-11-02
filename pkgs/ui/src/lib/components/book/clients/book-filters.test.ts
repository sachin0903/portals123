/** The clients list's search/segment/sort predicates and the header summary (a money figure).
 *
 *  The header line is the reason `bookSummary` exists: it used to pair the WHOLE book's client
 *  count with the FILTERED rows' AUM, so any search made the page state a total that did not
 *  belong to the population named beside it. Moved here with the module when the clients listing
 *  was extracted from apps/platform into the shared book components.
 */
import { describe, expect, it } from 'vitest';
import {
	bookSummary,
	inBookSegment,
	matchesClientQuery,
	visibleClientRows,
	type BookListRow
} from './book-filters';

const row = (over: Partial<BookListRow> = {}): BookListRow => ({
	type: 'individual',
	name: 'Theo Caldwell',
	title: null,
	first_name: 'Theo',
	surname: 'Caldwell',
	email: 'theo@caldwell.test',
	mobile: '+44 7700 900123',
	phone: '+44 117 900 0001',
	uid: 'con_abc123',
	advisers: ['Zoe Lead'],
	enabled: true,
	service_type: 'advised',
	account_count: 2,
	valued_account_count: 1,
	portfolio_value: 100_000,
	...over
});

describe('search', () => {
	it('matches the fields staff quote on a call — name, email, phone, reference, adviser', () => {
		const c = row();
		for (const q of ['cald', 'THEO@caldwell', '900123', 'con_abc', 'zoe']) {
			expect(matchesClientQuery(c, q), q).toBe(true);
		}
		expect(matchesClientQuery(c, 'nobody')).toBe(false);
	});

	it('searches EVERY number the contact holds, not just the primary mobile', () => {
		// A client rings from their work landline and the caller types what their screen showed
		// them. A search that only knew the mobile answered "no such client" about somebody who
		// was already on the line.
		const c = row();
		expect(matchesClientQuery(c, '9000001')).toBe(true);
		expect(matchesClientQuery(c, '+44 117 900 0001')).toBe(true);
		expect(matchesClientQuery(row({ phone: null }), '9000001')).toBe(false);
	});

	it('compares numbers on their DIGITS, so stored spacing does not have to be retyped', () => {
		// Stored `+44 7700 900123`, typed `900123` off a screen — or pasted back with its own
		// spacing. Neither is a substring of the other as text.
		const c = row();
		for (const q of ['900123', '07700900123', '07700 900123', '+447700900123'])
			expect(matchesClientQuery(c, q), q).toBe(true);
	});

	it('does not read a reference as a phone number', () => {
		// `abc123` is the tail of a client reference. Stripping its non-digits would make it a
		// search for "123", which is in most numbers in the book.
		const c = row({ uid: 'con_zzz', mobile: '+44 7700 900123', phone: null });
		expect(matchesClientQuery(c, 'abc123')).toBe(false);
	});

	it('an empty query matches everything, and whitespace is not a query', () => {
		expect(matchesClientQuery(row(), '')).toBe(true);
		expect(matchesClientQuery(row(), '   ')).toBe(true);
	});

	it('falls back to the organisation name for an organisation', () => {
		const org = row({ type: 'organisation', name: 'Caldwell Holdings', surname: null });
		expect(matchesClientQuery(org, 'holdings')).toBe(true);
	});
});

describe('segments', () => {
	const funded = row({ valued_account_count: 2 });
	const off = row({ enabled: false, valued_account_count: 2 });
	const exec = row({ service_type: 'execution_only' });

	it('"active" needs value AND an enabled record; "inactive" is the reachable other side', () => {
		expect(inBookSegment(funded, 'active')).toBe(true);
		expect(inBookSegment(off, 'active')).toBe(false);
		expect(inBookSegment(off, 'inactive')).toBe(true);
		expect(inBookSegment(funded, 'inactive')).toBe(false);
		// "Holds an account" is not "holds one that can carry value": a draft or closed wrapper
		// leaves the client outside Active, which is the distinction the retired Unfunded segment
		// used to carry on its own.
		expect(inBookSegment(row({ valued_account_count: 0, account_count: 1 }), 'active')).toBe(false);
	});

	it('splits by service type, and "all" (or anything unknown) admits everyone', () => {
		expect(inBookSegment(exec, 'execution_only')).toBe(true);
		expect(inBookSegment(exec, 'advised')).toBe(false);
		expect(inBookSegment(exec, 'all')).toBe(true);
		expect(inBookSegment(exec, 'nonsense')).toBe(true);
	});
});

describe('visibleClientRows', () => {
	const book = [
		row({ uid: 'a', surname: 'Ashworth', portfolio_value: 10, account_count: 1 }),
		row({ uid: 'b', surname: 'Beaumont', portfolio_value: 300, account_count: 3 }),
		row({ uid: 'c', surname: 'Caldwell', portfolio_value: 200, account_count: 2 })
	];

	it('sorts by AUM descending by default', () => {
		expect(visibleClientRows(book, '', 'all', 'aum').map((c) => c.uid)).toEqual(['b', 'c', 'a']);
	});

	it('sorts by name and by account count on request', () => {
		expect(visibleClientRows(book, '', 'all', 'name').map((c) => c.uid)).toEqual(['a', 'b', 'c']);
		expect(visibleClientRows(book, '', 'all', 'accounts').map((c) => c.uid)).toEqual([
			'b',
			'c',
			'a'
		]);
	});

	it('combines the segment and the query, and never mutates the input', () => {
		const order = book.map((c) => c.uid);
		expect(visibleClientRows(book, 'beau', 'advised', 'aum').map((c) => c.uid)).toEqual(['b']);
		expect(book.map((c) => c.uid)).toEqual(order);
	});
});

describe('bookSummary', () => {
	const money = (n: number) => `£${n.toLocaleString('en-GB')}`;
	const book = [row({ portfolio_value: 100 }), row({ portfolio_value: 300 })];

	it('states the whole book when nothing is filtered', () => {
		expect(bookSummary(book, 2, money, null)).toBe('2 clients · £400 under advice');
	});

	it('never pairs the whole-book count with a subset’s money', () => {
		const shown = [book[0]];
		const line = bookSummary(shown, 2, money, null);
		expect(line).toBe('1 of 2 clients · £100 under advice');
		expect(line).not.toContain('£400');
	});

	it('carries the as-at when the loader has one', () => {
		expect(bookSummary(book, 2, money, '14 Jul 2026')).toContain('values as at 14 Jul 2026');
	});
});
