import { describe, expect, it } from 'vitest';
import {
	groupRows,
	type DataTableGroupKey,
	type DataTableGroupRow,
	type DataTableRenderRow
} from './group.js';
import type { DataTableColumn, DataTableSort } from './types.js';

type Doc = { uid: string; name: string; client: string | null; type: string };

const columns: DataTableColumn<Doc>[] = [
	{ key: 'name', header: 'Name', sortable: true },
	{ key: 'type', header: 'Type', sortable: true },
	{ key: 'client', header: 'Client', sortable: true, sortValue: (d) => d.client }
];
const byName: DataTableSort = { key: 'name', dir: 'asc' };
const doc = (uid: string, name: string, client: string | null, type: string): Doc => ({
	uid,
	name,
	client,
	type
});
const docs = [
	doc('a', 'Passport', 'Zoe Ash', 'Identity'),
	doc('b', 'Annual statement', 'Zoe Ash', 'Statement'),
	doc('c', 'Driving licence', 'Zoe Ash', 'Identity'),
	doc('d', 'Suitability report', 'Adam Bell', 'Report')
];

const clientThenType = (row: Doc): DataTableGroupKey[] => [
	{ key: row.client ?? '_none', label: row.client ?? 'No client', last: row.client === null },
	{ key: row.type, label: row.type }
];
const byType = (row: Doc): DataTableGroupKey[] => [{ key: row.type, label: row.type }];

type Entry = DataTableRenderRow<Doc>;
type Opts = { rows?: Doc[]; sort?: DataTableSort; shut?: string[]; presorted?: boolean };
const run = (group: (row: Doc) => DataTableGroupKey[], o: Opts = {}) =>
	groupRows(o.rows ?? docs, group, columns, o.sort ?? byName, new Set(o.shut), o.presorted);
const bands = (entries: Entry[], depth?: number) =>
	entries.filter(
		(e): e is DataTableGroupRow<Doc> =>
			e.kind === 'group' && (depth === undefined || e.depth === depth)
	);
const labels = (entries: Entry[], depth?: number) => bands(entries, depth).map((b) => b.label);
const names = (entries: Entry[]) => entries.flatMap((e) => (e.kind === 'row' ? [e.row.name] : []));
const shape = (entries: Entry[]) =>
	entries.map((e) => {
		const pad = '  '.repeat(e.depth);
		return e.kind === 'group' ? `${pad}[${e.label} ${e.count}]` : `${pad}${e.row.name}`;
	});

describe('groupRows', () => {
	it('nests bands, sorts rows inside each one, and numbers bands for `aria-posinset`', () => {
		const out = run(clientThenType);
		expect(shape(out)).toEqual([
			'[Adam Bell 1]',
			'  [Report 1]',
			'    Suitability report',
			'[Zoe Ash 3]',
			'  [Identity 2]',
			'    Driving licence',
			'    Passport',
			'  [Statement 1]',
			'    Annual statement'
		]);
		expect(bands(out, 0).map((b) => [b.position, b.setSize])).toEqual([
			[1, 2],
			[2, 2]
		]);
	});

	it('a shut band keeps its header and every row beneath it, but renders none of them', () => {
		const out = run(clientThenType, { shut: ['Zoe Ash'] });
		expect(shape(out)).toEqual([
			'[Adam Bell 1]',
			'  [Report 1]',
			'    Suitability report',
			'[Zoe Ash 3]'
		]);
		expect(bands(out, 0)[1].rows.map((r) => r.uid)).toEqual(['a', 'b', 'c']);
	});

	it('shuts a nested band by its full path, not its bare key', () => {
		expect(names(run(clientThenType, { shut: ['Zoe Ash/Identity'] }))).toEqual([
			'Suitability report',
			'Annual statement'
		]);
		expect(names(run(clientThenType, { shut: ['Identity'] }))).toHaveLength(4);
	});

	it('leaves rows with no path loose above the bands', () => {
		const optional = (row: Doc): DataTableGroupKey[] =>
			row.client ? [{ key: row.client, label: row.client }] : [];
		const out = run(optional, { rows: [...docs, doc('e', 'Scan', null, 'Identity')] });
		expect(out[0]).toMatchObject({ kind: 'row', row: { name: 'Scan' } });
		expect(names(out)).toHaveLength(5);
	});

	it('a column sort orders rows within bands, never the bands', () => {
		const desc = run(byType, { sort: { key: 'name', dir: 'desc' } });
		expect(labels(desc)).toEqual(['Identity', 'Report', 'Statement']);
		expect(names(desc).slice(0, 2)).toEqual(['Passport', 'Driving licence']);
	});

	it('leaves the given order alone when presorted', () => {
		expect(names(run(byType, { presorted: true })).slice(0, 2)).toEqual([
			'Passport',
			'Driving licence'
		]);
	});

	it.each([
		['a key holding the separator', [{ key: 'a/b', label: 'Slashed' }], ['a', 'b']],
		['an empty key', ['', 'b'], [{ key: 'b', label: 'Top' }]]
	])('gives two bands two paths with %s', (_, first, second) => {
		const step = (k: string | DataTableGroupKey) =>
			typeof k === 'string' ? { key: k, label: k } : k;
		const rows = [doc('1', 'One', null, 'x'), doc('2', 'Two', null, 'y')];
		const paths = bands(run((d) => (d.uid === '1' ? first : second).map(step), { rows })).map(
			(b) => b.path
		);
		expect(new Set(paths).size).toBe(paths.length);
	});

	it('holds two bands with the same label in a stable order', () => {
		const named = (d: Doc): DataTableGroupKey[] => [{ key: d.uid, label: 'John Smith' }];
		const rows = [doc('1', 'One', null, 'x'), doc('2', 'Two', null, 'y')];
		const paths = (r: Doc[]) => bands(run(named, { rows: r })).map((b) => b.path);
		expect(paths(rows)).toEqual(paths([...rows].reverse()));
	});

	it('puts a caller `order` ahead of the alphabet, pins `last`, and carries the icon', () => {
		const Glyph = (() => null) as unknown as NonNullable<DataTableGroupKey['icon']>;
		const ordered = (d: Doc): DataTableGroupKey[] => [
			d.type === 'Identity'
				? { key: 'z', label: 'Zebra', order: 1, icon: Glyph }
				: d.type === 'Report'
					? { key: 'a', label: 'Apple', order: 2 }
					: { key: 'm', label: 'Middle', last: true }
		];
		const out = bands(run(ordered));
		expect(out.map((b) => b.label)).toEqual(['Zebra', 'Apple', 'Middle']);
		expect(out.map((b) => b.icon)).toEqual([Glyph, undefined, undefined]);
	});

	describe('a level that declares its column', () => {
		const declared = (row: Doc): DataTableGroupKey[] =>
			clientThenType(row).map((step, i) => ({ ...step, column: i === 0 ? 'client' : 'type' }));
		const rows = [
			...docs,
			doc('e', 'Scan', null, 'Identity'),
			doc('f', 'Letter', 'Mia Cole', 'Statement')
		];
		const asc = ['Adam Bell', 'Mia Cole', 'Zoe Ash', 'No client'];
		const sorted = (key: string, dir: 'asc' | 'desc', presorted = false) =>
			run(declared, { rows, sort: { key, dir }, presorted });

		it.each([false, true])('orders that level’s bands by label (presorted %s)', (presorted) => {
			expect(labels(sorted('client', 'asc', presorted), 0)).toEqual(asc);
			expect(labels(sorted('client', 'desc', presorted), 0)).toEqual([
				'Zoe Ash',
				'Mia Cole',
				'Adam Bell',
				'No client'
			]);
		});

		it('orders only the level that column names', () => {
			const zoe = (out: Entry[]) =>
				bands(out, 1)
					.filter((b) => b.path.startsWith('Zoe Ash/'))
					.map((b) => b.label);
			expect(zoe(sorted('client', 'desc'))).toEqual(['Identity', 'Statement']);
			expect(zoe(sorted('type', 'desc'))).toEqual(['Statement', 'Identity']);
			expect(labels(sorted('type', 'desc'), 0)).toEqual(asc);
		});
	});

	it('returns nothing for no rows', () => {
		expect(run(clientThenType, { rows: [] })).toEqual([]);
	});
});
