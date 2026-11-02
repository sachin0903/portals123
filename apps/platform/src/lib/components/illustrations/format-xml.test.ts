import { describe, expect, it } from 'vitest';
import { formatXml } from './format-xml';

describe('formatXml', () => {
	it('indents nested elements and keeps leaves on one line', () => {
		expect(formatXml('<a><b><c>1</c></b></a>')).toBe(
			['<a>', '  <b>', '    <c>1</c>', '  </b>', '</a>'].join('\n')
		);
	});

	it('keeps the declaration flush and does not indent under it', () => {
		expect(formatXml("<?xml version='1.0'?><message><header>CTC1</header></message>")).toBe(
			["<?xml version='1.0'?>", '<message>', '  <header>CTC1</header>', '</message>'].join('\n')
		);
	});

	it('treats a self-closing element as a leaf', () => {
		expect(formatXml('<a><b/><c>2</c></a>')).toBe(
			['<a>', '  <b/>', '  <c>2</c>', '</a>'].join('\n')
		);
	});

	it('never lets depth go negative on unbalanced input', () => {
		// Malformed input is a display problem, not a crash: the point of not writing a parser.
		expect(formatXml('</a></b><c>3</c>')).toBe(['</a>', '</b>', '<c>3</c>'].join('\n'));
	});

	it('leaves a single element untouched', () => {
		expect(formatXml('<a>1</a>')).toBe('<a>1</a>');
	});

	it('is stable when applied twice', () => {
		const once = formatXml('<a><b>1</b><b>2</b></a>');
		expect(formatXml(once)).toBe(once);
	});
});
