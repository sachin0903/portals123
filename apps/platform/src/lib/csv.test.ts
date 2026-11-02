import { describe, it, expect } from 'vitest';
import { parseCsvLine, parseInlineTable, toCsvCell, toCsvLine, parseCsvFile } from './csv';

describe('parseCsvLine', () => {
	it('splits plain comma-separated values and trims them', () => {
		expect(parseCsvLine('2025/26, 20000')).toEqual(['2025/26', '20000']);
	});

	it('keeps commas inside double-quoted values (the £325,000 bug)', () => {
		expect(parseCsvLine('2025/26, "£325,000"')).toEqual(['2025/26', '£325,000']);
	});

	it('treats "" inside a quoted value as a literal quote', () => {
		expect(parseCsvLine('"a ""b"" c", d')).toEqual(['a "b" c', 'd']);
	});

	it('counts a trailing empty cell', () => {
		expect(parseCsvLine('a,b,')).toEqual(['a', 'b', '']);
	});
});

describe('parseInlineTable', () => {
	it('parses a clean table with no ragged rows', () => {
		const t = parseInlineTable('Tax year, ISA limit', '2025/26, 20000\n2026/27, 20000');
		expect(t.columns).toEqual(['Tax year', 'ISA limit']);
		expect(t.rows).toEqual([
			['2025/26', '20000'],
			['2026/27', '20000']
		]);
		expect(t.raggedRows).toEqual([]);
	});

	it('flags a row split by an unquoted comma instead of silently corrupting it', () => {
		const t = parseInlineTable('Tax year, ISA limit', '2025/26, £325,000');
		// "£325,000" split into two cells → 3 total against 2 columns.
		expect(t.rows[0]).toEqual(['2025/26', '£325', '000']);
		expect(t.raggedRows).toEqual([{ line: 1, got: 3 }]);
	});

	it('accepts the quoted form of the same value', () => {
		const t = parseInlineTable('Tax year, ISA limit', '2025/26, "£325,000"');
		expect(t.rows[0]).toEqual(['2025/26', '£325,000']);
		expect(t.raggedRows).toEqual([]);
	});

	it('ignores blank lines and reports 1-based line numbers', () => {
		const t = parseInlineTable('a, b', 'x, y\n\nz'); // line 3 ("z") is short
		expect(t.raggedRows).toEqual([{ line: 3, got: 1 }]);
	});
});

describe('toCsvCell / toCsvLine', () => {
	it('leaves plain values unquoted', () => {
		expect(toCsvCell('2025/26')).toBe('2025/26');
		expect(toCsvLine(['a', 'b'])).toBe('a,b');
	});

	it('quotes values containing a comma, quote or newline', () => {
		expect(toCsvCell('£325,000')).toBe('"£325,000"');
		expect(toCsvCell('say "hi"')).toBe('"say ""hi"""');
		expect(toCsvLine(['2025/26', '£325,000'])).toBe('2025/26,"£325,000"');
	});

	it('round-trips through parseCsvLine', () => {
		const cells = ['2025/26', '£325,000', 'a "b" c', 'plain'];
		expect(parseCsvLine(toCsvLine(cells))).toEqual(cells);
	});
});

describe('parseCsvFile', () => {
	it('takes the first line as headers and the rest as rows', () => {
		const { columns, rows } = parseCsvFile(
			'Tax year,ISA limit\n2025/26,"£20,000"\n2026/27,"£20,000"'
		);
		expect(columns).toEqual(['Tax year', 'ISA limit']);
		expect(rows).toEqual([
			['2025/26', '£20,000'],
			['2026/27', '£20,000']
		]);
	});

	it('pads short rows and truncates long rows to the header width', () => {
		const { rows } = parseCsvFile('a,b,c\n1,2\n1,2,3,4');
		expect(rows).toEqual([
			['1', '2', ''],
			['1', '2', '3']
		]);
	});

	it('skips blank lines and handles an empty file', () => {
		expect(parseCsvFile('')).toEqual({ columns: [], rows: [] });
		expect(parseCsvFile('a,b\n\n1,2\n').rows).toEqual([['1', '2']]);
	});
});
