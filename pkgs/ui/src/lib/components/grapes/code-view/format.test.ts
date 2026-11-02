import { describe, expect, it } from 'vitest';
import { formatHtml, hasTopLevelText } from './format.js';

// Whitespace may be added between ELEMENTS, never inside TEXT — a newline there changes the copy a client receives when parsed back into the canvas (`format.ts` has the argument in full).
describe('formatHtml', () => {
	it('indents nested elements, one per line', () => {
		expect(formatHtml('<table><tr><td></td></tr></table>')).toBe(
			['<table>', '  <tr>', '    <td></td>', '  </tr>', '</table>'].join('\n')
		);
	});

	it('leaves an element with text in it on ONE line, verbatim', () => {
		const html = '<td><p>Hello <b>Craig</b>, welcome.</p></td>';
		expect(formatHtml(html)).toBe(
			['<td>', '  <p>Hello <b>Craig</b>, welcome.</p>', '</td>'].join('\n')
		);
	});

	it('keeps a Jinja run inside copy untouched', () => {
		const html = '<div><p>{% if x %}Hi {{ first_name }}{% endif %}</p></div>';
		expect(formatHtml(html)).toContain('<p>{% if x %}Hi {{ first_name }}{% endif %}</p>');
	});

	it('handles a void tag without hunting for a close tag', () => {
		// `readOpenTag` reports a void tag self-closing, or `findCloseTag` would hunt to EOF for `</img>`.
		expect(formatHtml('<div><img src="a.png"><br></div>')).toBe(
			['<div>', '  <img src="a.png">', '  <br>', '</div>'].join('\n')
		);
	});

	it('survives a `>` inside an attribute value', () => {
		// A real case: a condition's label reads `If x is "&gt;5"`, and a naive scan would end the tag there.
		const html = '<div data-if-label="If x is &gt;5"><p>Hi</p></div>';
		expect(formatHtml(html).split('\n')[0]).toBe('<div data-if-label="If x is &gt;5">');
	});

	it('emits an unbalanced element rather than wrapping to the end of the document', () => {
		const out = formatHtml('<div><p>one</div><p>two</p>');
		expect(out).toContain('two');
		expect(out.split('\n').length).toBeGreaterThan(1);
	});

	it('leaves <style> content alone — it is not markup', () => {
		const html = '<div><style>a > b { color: red }</style></div>';
		expect(formatHtml(html)).toContain('<style>a > b { color: red }</style>');
	});

	it('is idempotent on its own output', () => {
		const once = formatHtml('<table><tr><td><p>Hi <b>x</b></p></td></tr></table>');
		expect(formatHtml(once)).toBe(once);
	});

	it('answers empty for empty', () => {
		expect(formatHtml('')).toBe('');
	});
});

describe('hasTopLevelText', () => {
	it('is true for mixed content and false for elements alone', () => {
		expect(hasTopLevelText('Hello <b>x</b>')).toBe(true);
		expect(hasTopLevelText('<b>x</b><i>y</i>')).toBe(false);
		expect(hasTopLevelText('  <b>x</b>\n  <i>y</i>  ')).toBe(false);
	});

	it('treats a stray `<` in copy as text', () => {
		// "a < b" is copy; a formatter that walked past the `<` would have no idea where the element ends.
		expect(hasTopLevelText('a < b')).toBe(true);
	});

	it('does not mistake text INSIDE a child for its own', () => {
		// `<td><p>Hi</p></td>`: the td may be broken open, the p may not.
		expect(hasTopLevelText('<p>Hi</p>')).toBe(false);
	});
});
