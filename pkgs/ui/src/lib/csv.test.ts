import { describe, expect, it } from 'vitest';
import { csvCell } from './csv';

describe('csvCell', () => {
	it('returns an empty string for null/undefined', () => {
		expect(csvCell(null)).toBe('');
		expect(csvCell(undefined)).toBe('');
	});

	it('stringifies objects as JSON, quoted because JSON itself carries quotes', () => {
		expect(csvCell({ a: 1 })).toBe('"{""a"":1}"');
	});

	it('neutralises a leading formula character with a text-marker prefix', () => {
		expect(csvCell('=cmd|calc')).toBe("'=cmd|calc");
		expect(csvCell('+1')).toBe("'+1");
		expect(csvCell('-1')).toBe("'-1");
		expect(csvCell('@mention')).toBe("'@mention");
	});

	it('quotes a value containing a delimiter, quote or newline', () => {
		expect(csvCell('a,b')).toBe('"a,b"');
		expect(csvCell('say "hi"')).toBe('"say ""hi"""');
		expect(csvCell('line1\nline2')).toBe('"line1\nline2"');
	});

	it('leaves a plain value untouched', () => {
		expect(csvCell('Oakden Holdings Ltd')).toBe('Oakden Holdings Ltd');
		expect(csvCell(42)).toBe('42');
	});
});
