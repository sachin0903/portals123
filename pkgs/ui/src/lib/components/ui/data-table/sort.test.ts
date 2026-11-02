import { describe, expect, it } from 'vitest';
import { compareValues, defaultSort, nextSort, sortRows } from './sort.js';
import type { DataTableColumn } from './types.js';

interface Row {
	name: string;
	value: number | null;
	adviser?: string;
	opened?: Date;
}

const columns: DataTableColumn<Row>[] = [
	{ key: 'name', header: 'Name', sortable: true },
	{ key: 'value', header: 'Value', sortable: true, defaultDir: 'desc' },
	{ key: 'adviser', header: 'Adviser', sortable: true },
	{ key: 'opened', header: 'Opened', sortable: true },
	{
		key: 'display',
		header: 'Display',
		sortable: true,
		sortValue: (r) => r.name.toLowerCase()
	}
];

const rows: Row[] = [
	{ name: 'Account 10', value: 500, adviser: 'Craig' },
	{ name: 'Account 2', value: 1500, adviser: '' },
	{ name: 'account 9', value: null, adviser: 'Andy' }
];

const names = (rs: Row[]) => rs.map((r) => r.name);

describe('sortRows', () => {
	it('returns the input untouched when there is no sort', () => {
		expect(sortRows(rows, columns, null)).toBe(rows);
	});

	it('returns the input untouched when the sort key matches no column', () => {
		// A stale key (e.g. a column removed behind a feature flag) must not blank the table.
		expect(sortRows(rows, columns, { key: 'gone', dir: 'asc' })).toBe(rows);
	});

	it('does not mutate the source array', () => {
		const source = [...rows];
		sortRows(source, columns, { key: 'name', dir: 'asc' });
		expect(source).toEqual(rows);
	});

	it('collates names numerically so "Account 2" precedes "Account 10"', () => {
		expect(names(sortRows(rows, columns, { key: 'name', dir: 'asc' }))).toEqual([
			'Account 2',
			'account 9',
			'Account 10'
		]);
	});

	it('sorts numbers numerically, not lexically', () => {
		expect(names(sortRows(rows, columns, { key: 'value', dir: 'desc' }))).toEqual([
			'Account 2',
			'Account 10',
			'account 9'
		]);
	});

	it('keeps blanks last in both directions', () => {
		// null value and '' adviser are both blank; flipping direction must not promote them.
		expect(names(sortRows(rows, columns, { key: 'value', dir: 'asc' })).at(-1)).toBe('account 9');
		expect(names(sortRows(rows, columns, { key: 'value', dir: 'desc' })).at(-1)).toBe('account 9');
		expect(names(sortRows(rows, columns, { key: 'adviser', dir: 'asc' })).at(-1)).toBe('Account 2');
		expect(names(sortRows(rows, columns, { key: 'adviser', dir: 'desc' })).at(-1)).toBe(
			'Account 2'
		);
	});

	it('uses the column sortValue when the cell renders something else', () => {
		expect(names(sortRows(rows, columns, { key: 'display', dir: 'asc' }))).toEqual([
			'Account 2',
			'account 9',
			'Account 10'
		]);
	});

	it('orders dates chronologically', () => {
		const dated: Row[] = [
			{ name: 'b', value: 1, opened: new Date('2026-03-01') },
			{ name: 'a', value: 1, opened: new Date('2025-01-01') }
		];
		expect(names(sortRows(dated, columns, { key: 'opened', dir: 'asc' }))).toEqual(['a', 'b']);
	});
});

describe('nextSort', () => {
	const value = columns[1];
	const name = columns[0];

	it('starts a new column at its default direction', () => {
		expect(nextSort(null, value)).toEqual({ key: 'value', dir: 'desc' });
		expect(nextSort(null, name)).toEqual({ key: 'name', dir: 'asc' });
	});

	it('flips direction when the same column is clicked again', () => {
		expect(nextSort({ key: 'value', dir: 'desc' }, value)).toEqual({ key: 'value', dir: 'asc' });
		expect(nextSort({ key: 'value', dir: 'asc' }, value)).toEqual({ key: 'value', dir: 'desc' });
	});

	it('resets to the default direction when switching columns', () => {
		expect(nextSort({ key: 'name', dir: 'desc' }, value)).toEqual({ key: 'value', dir: 'desc' });
	});
});

describe('defaultSort', () => {
	it('is null without a sortable sortDefault column', () => {
		expect(defaultSort(columns)).toBeNull();
		expect(defaultSort([{ key: 'ref', header: 'Ref', sortDefault: true }])).toBeNull();
	});

	it('picks the sortDefault column at its default direction', () => {
		const marked = columns.map((c) => (c.key === 'value' ? { ...c, sortDefault: true } : c));
		expect(defaultSort(marked)).toEqual({ key: 'value', dir: 'desc' });
	});
});

describe('compareValues', () => {
	it('is case-insensitive for names', () => {
		expect(compareValues('alice', 'Alice')).toBe(0);
	});

	it('orders numbers by magnitude', () => {
		expect(compareValues(2, 10)).toBeLessThan(0);
	});
});
