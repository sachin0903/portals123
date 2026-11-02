/**
 * The shared JSON syntax highlighter behind the Events + audit-log "Details" panes. The invariant
 * that matters most is LOSSLESSNESS — each line must be exactly its segments concatenated, so the
 * raw JSON is never silently mangled — plus the tone rules (key vs string value, numbers incl.
 * exponents as ONE token, muted literals) and that an escaped quote never splits a string (which is
 * what keeps a captured string from ever being mis-tokenised).
 */
import { describe, expect, it } from 'vitest';
import { highlightJson } from './highlight-json.js';

const flat = (line: string) => highlightJson(line)[0];
const seg = (line: string, text: string) => flat(line).find((s) => s.text === text);

describe('highlightJson', () => {
	it('splits into one row per line', () => {
		expect(highlightJson('{\n  "x": 1\n}')).toHaveLength(3);
	});

	it('loses nothing — each line is exactly its segments concatenated', () => {
		const src = '{\n  "name": "Ada",\n  "age": -3.5e2,\n  "ok": true,\n  "x": null\n}';
		highlightJson(src).forEach((segs, i) => {
			expect(segs.map((s) => s.text).join('')).toBe(src.split('\n')[i]);
		});
	});

	it('tones a key as info and a string value as success', () => {
		const line = '  "name": "Ada"';
		expect(seg(line, '"name":')?.cls).toBe('text-info');
		expect(seg(line, '"Ada"')?.cls).toBe('text-success');
	});

	it('tones numbers as warning, keeping an exponent as ONE token', () => {
		expect(seg('"n": 1.5e3', '1.5e3')?.cls).toBe('text-warning');
		expect(seg('"n": -42', '-42')?.cls).toBe('text-warning');
	});

	it('mutes the literals true / false / null', () => {
		expect(seg('"a": true', 'true')?.cls).toBe('text-muted-foreground');
		expect(seg('"a": false', 'false')?.cls).toBe('text-muted-foreground');
		expect(seg('"a": null', 'null')?.cls).toBe('text-muted-foreground');
	});

	it('keeps an escaped quote inside a string in one success-toned segment', () => {
		const line = '"note": "a \\"quote\\" here"';
		const strings = flat(line).filter((s) => s.cls === 'text-success');
		expect(strings).toHaveLength(1);
		expect(strings[0].text).toContain('quote');
		// …and still loses nothing.
		expect(
			flat(line)
				.map((s) => s.text)
				.join('')
		).toBe(line);
	});
});
