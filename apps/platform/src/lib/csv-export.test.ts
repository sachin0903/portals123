import { describe, expect, it } from 'vitest';
import { csvFile, csvResponse, exportCell, stamp } from './csv-export';

describe('exportCell', () => {
	it('leaves a plain value alone', () => {
		expect(exportCell('Halloway')).toBe('Halloway');
	});

	it('quotes a value carrying a delimiter, quote or newline', () => {
		// The reason CSV needs a writer at all: a normal UK figure has a comma in it.
		expect(exportCell('£325,000')).toBe('"£325,000"');
		expect(exportCell('a "quoted" word')).toBe('"a ""quoted"" word"');
		expect(exportCell('line one\nline two')).toBe('"line one\nline two"');
	});

	it('renders nothing for null and undefined, not the words', () => {
		expect(exportCell(null)).toBe('');
		expect(exportCell(undefined)).toBe('');
		// …but a real zero is data.
		expect(exportCell(0)).toBe('0');
		expect(exportCell(false)).toBe('false');
	});

	describe('CSV injection', () => {
		// The whole reason this module is separate from csv.ts. Every cell in an export was typed by
		// a person, and the file is the one artefact a reader is guaranteed to open in Excel.
		it('neutralises every formula lead character', () => {
			for (const lead of ['=', '+', '-', '@', '\t', '\r']) {
				expect(exportCell(`${lead}HYPERLINK("http://x")`)).toMatch(/^"?'/);
			}
		});

		it('defuses the classic payloads', () => {
			expect(exportCell('=HYPERLINK("http://evil","click")')).toBe(
				`"'=HYPERLINK(""http://evil"",""click"")"`
			);
			expect(exportCell("=cmd|' /c calc'!A1")).toContain(`'=cmd|`);
		});

		it('does not maul a value that merely CONTAINS one', () => {
			// Only a LEADING character starts a formula; quoting the rest would be noise.
			expect(exportCell('Smith-Jones')).toBe('Smith-Jones');
			expect(exportCell('a@b.com')).toBe('a@b.com');
		});

		it('still guards a negative number, and that is deliberate', () => {
			// `-42` is prefixed too. Losing the numeric type on a handful of cells is the price of a
			// rule with no exceptions; an exception for "looks numeric" is exactly the gap a payload
			// is shaped to fit through.
			expect(exportCell(-42)).toBe(`'-42`);
		});
	});
});

describe('csvFile', () => {
	it('writes a header, CRLF endings and a trailing newline', () => {
		const out = csvFile(['Name', 'Value'], [['Halloway', 1]]);
		expect(out.endsWith('\r\n')).toBe(true);
		expect(out).toContain('Name,Value\r\nHalloway,1');
	});

	it('starts with a BOM', () => {
		// Excel on Windows reads a BOM-less UTF-8 file as the system codepage, so every £ and every
		// accented name arrives mangled.
		expect(csvFile(['A'], [['Åsa']]).charCodeAt(0)).toBe(0xfeff);
	});

	it('escapes the header too', () => {
		// A column label is usually ours, but a facet-derived one need not be.
		expect(csvFile(['a,b'], [])).toContain('"a,b"');
	});
});

describe('csvResponse', () => {
	it('names the file and refuses to be cached', () => {
		const res = csvResponse('clients-2026-08-26.csv', 'a\r\n');
		expect(res.headers.get('content-type')).toBe('text/csv; charset=utf-8');
		expect(res.headers.get('content-disposition')).toBe(
			'attachment; filename="clients-2026-08-26.csv"'
		);
		// A firm's whole book in a shared cache is the leak this prevents.
		expect(res.headers.get('cache-control')).toBe('private, no-store');
	});
});

describe('stamp', () => {
	it('is a sortable date', () => {
		expect(stamp(new Date('2026-08-26T14:03:00Z'))).toBe('2026-08-26');
	});
});
