/**
 * The dashboard table's pager highlights the page its rows came from.
 *
 * In server mode the rows are one page of the book and the page is the URL's `offset`. The pager
 * used to keep its own `page = 1` that nothing wrote to, so the strip read "26–50 of 798" while
 * page 1 stayed highlighted — a pager that looked like it had not worked, over rows that had.
 * Rendered rather than source-matched: the label and the `aria-current` marker have to agree.
 */
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import ClientsTable from './clients-table.svelte';
import { emptyQuery } from '../listing/query.js';
import type { BookRow } from './book.js';

const row = (id: number): BookRow => ({
	id,
	uid: `cnt_${id}`,
	type: 'individual',
	name: null,
	title: null,
	first_name: 'Max',
	surname: `Client ${id}`,
	email: null,
	advisers: [],
	enabled: true,
	service_type: 'advised',
	account_count: 1,
	valued_account_count: 1,
	portfolio_value: 1000,
	cash: 0,
	growth: 0
});

const rows = Array.from({ length: 25 }, (_, i) => row(26 + i));

const serverPage = (offset: number) =>
	render(ClientsTable, {
		props: {
			rows,
			empty: 'No clients.',
			criteria: { query: '', status: [], adviser: [], type: [] },
			listing: { ...emptyQuery(['status', 'adviser', 'type']), offset },
			matchCount: 798,
			onquery: () => {},
			pageHref: (page: number) => `?offset=${(page - 1) * 25}`
		}
	}).body;

/** The text of the pager link carrying `aria-current="page"`. */
function currentPage(html: string): string | null {
	const match = html.match(/<a[^>]*aria-current="page"[^>]*>(\d+)<\/a>/);
	return match ? match[1] : null;
}

describe('dashboard clients table pager (server mode)', () => {
	it('highlights the page the offset is on, not page 1', () => {
		const html = serverPage(25);
		expect(html).toContain('26–50 of 798');
		expect(currentPage(html)).toBe('2');
	});

	it('still highlights page 1 at the start', () => {
		const html = serverPage(0);
		expect(html).toContain('1–25 of 798');
		expect(currentPage(html)).toBe('1');
	});
});

/** A local (complete-array) mount: no `listing`/`onquery`, so the rows render as given. */
const local = (rows: BookRow[]) =>
	render(ClientsTable, {
		props: {
			rows,
			empty: 'No clients.',
			criteria: { query: '', status: [], adviser: [], type: [] }
		}
	}).body;

describe('dashboard clients table — the same book as /clients', () => {
	it('prints the date of birth under the name, else where the client is', () => {
		expect(local([{ ...row(1), dob: '1962-08-05' }])).toContain('5 Aug 1962');
		const located = local([
			{
				...row(2),
				dob: null,
				address: { line1: '1 Meneage St', city: 'Helston', postcode: 'TR13 9PU' }
			}
		]);
		expect(located).toContain('Helston, TR13 9PU');
		expect(located).not.toContain('Meneage');
		expect(local([row(3)])).not.toContain('Helston');
	});

	it('prints growth under the value while the growth column is hidden', () => {
		const html = local([{ ...row(1), portfolio_value: 125_000, growth: 8_000 }]);
		expect(html).toContain('hidden @5xl:table-cell');
		expect(html).toContain('block @5xl:hidden');
		expect(html).toContain('+£8,000');
	});

	it('shows the wrappers held as tags, with the overflow as a count, not a bare number', () => {
		const html = local([
			{ ...row(1), account_count: 6, account_types: ['GIA', 'ISA', 'JISA', 'SIPP'] }
		]);
		for (const wrapper of ['GIA', 'ISA', 'JISA']) expect(html).toContain(`>${wrapper}<`);
		expect(html).toContain('+1');
		expect(html).toContain('GIA, ISA, JISA, SIPP');
		expect(html).toContain('6 accounts');
		expect(html).not.toMatch(/tabular-nums">6</);
	});

	it('says nothing rather than zero when a client holds no accounts', () => {
		const html = local([{ ...row(1), account_count: 0, account_types: [] }]);
		expect(html).not.toContain('>GIA<');
		expect(html).not.toMatch(/>0</);
	});
});
