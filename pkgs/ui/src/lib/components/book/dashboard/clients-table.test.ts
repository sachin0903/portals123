import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { filterBook, type BookFilters, type BookRow } from './book.js';

describe('dashboard clients table', () => {
	it('keeps client references on the record rather than every listing row', () => {
		const source = readFileSync(new URL('./clients-table.svelte', import.meta.url), 'utf8');

		expect(source).not.toContain('providerReference');
		expect(source).not.toContain('<RecordId');
	});
});

const client = (over: Partial<BookRow>): BookRow =>
	({
		uid: 'cnt_1',
		type: 'individual',
		first_name: 'Max',
		advisers: [],
		account_count: 2,
		valued_account_count: 2,
		portfolio_value: 0,
		...over
	}) as BookRow;
const unfiltered: BookFilters = { query: '', status: [], adviser: [], type: [] };

describe('the dashboard book narrows by portfolio value', () => {
	const rows = [
		client({ uid: 'small', portfolio_value: 50_000 }),
		client({ uid: 'mid', advisers: ['Cole'], portfolio_value: 1_000_000 }),
		client({ uid: 'large', portfolio_value: 2_000_000 })
	];
	const kept = (f: Partial<BookFilters>) =>
		filterBook(rows, { ...unfiltered, ...f }).map((c) => c.uid);

	it('keeps what is inside the range, includes both ends, and takes one open end', () => {
		expect(kept({ value: ['100000', '1000000'] })).toEqual(['mid']);
		expect(kept({ value: ['1000000', ''] })).toEqual(['mid', 'large']);
		expect(kept({ value: ['', '1000000'] })).toEqual(['small', 'mid']);
		expect(kept({ value: ['', ''] })).toHaveLength(3);
		expect(kept({ adviser: ['Cole'], value: ['1000000', ''] })).toEqual(['mid']);
	});

	it('leaves an UNVALUED client out of every value answer, rather than calling them nought', () => {
		// A client with nothing valued carries `portfolio_value: 0`, and reading that as zero pounds
		// sweeps the whole unpriced tail into "under £10,000".
		const unvalued = [client({ uid: 'unvalued', valued_account_count: 0 })];
		expect(filterBook(unvalued, { ...unfiltered, value: ['0', '10000'] })).toHaveLength(0);
		expect(filterBook(unvalued, unfiltered)).toHaveLength(1);
	});

	it('carries the range through the URL POSITIONALLY, as a money range', () => {
		// The ordinary cleaning drops blanks, so `['', '3000']` — "up to 3,000" — would arrive as
		// `['3000']`, which the far end reads as "from 3,000".
		const svelte = readFileSync(new URL('./clients-table.svelte', import.meta.url), 'utf8');
		expect(svelte).toContain("kind: 'numberrange'");
		expect(svelte).toContain('barValueFrom(listing, BAR_KEYS, RANGE_KEYS)');
		expect(svelte).toContain('withBarValue(listing, BAR_KEYS, next, RANGE_KEYS)');
	});
});
