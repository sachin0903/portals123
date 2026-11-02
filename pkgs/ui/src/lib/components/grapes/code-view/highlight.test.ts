import { describe, expect, it } from 'vitest';
import { CODE_CLASSES, highlightHtml } from './highlight.js';

const text = (line: { text: string }[]) => line.map((s) => s.text).join('');
const classOf = (line: { text: string; cls: string }[], token: string) =>
	line.find((s) => s.text === token)?.cls;

// The invariant: segments reproduce the input exactly (as `highlight-json.test.ts` pins) — a dropped character would silently show the author markup not in their document.
describe('highlightHtml', () => {
	it('reproduces every line exactly', () => {
		const src = [
			'<table role="presentation" width="100%">',
			'  <tr>',
			'    <td style="padding:10px" data-if="%7B%22rules%22%3A%5B%5D%7D">',
			'      <p>Hi {{ first_name }}, a < b and {% if x %}yes{% endif %}</p>',
			'    </td>',
			'  </tr>',
			'</table>'
		].join('\n');
		const lines = highlightHtml(src);
		expect(lines.map(text).join('\n')).toBe(src);
	});

	it('keeps one array per line, so the gutter numbers line up', () => {
		const src = '<div>\n<p>a</p>\n</div>';
		expect(highlightHtml(src)).toHaveLength(3);
	});

	it('flags the CALLER’s OWN attributes differently from everything else', () => {
		// The whole point of a bespoke highlighter: these are what the panel edits the document
		// through, and a hand-editing author must be able to SEE which attributes not to drop.
		const markers = ['data-if', 'data-field'];
		const [line = []] = highlightHtml('<td style="x" data-if="y" data-field="amount">', markers);
		expect(classOf(line, 'data-if')).toBe(CODE_CLASSES.MARKER);
		expect(classOf(line, 'data-field')).toBe(CODE_CLASSES.MARKER);
		expect(classOf(line, 'style')).toBe(CODE_CLASSES.ATTR);
	});

	it('takes the marker list from the CALLER, and colours none by default', () => {
		// Two editors own different attributes, so a list baked in here would confidently colour one
		// editor's bookkeeping and miss the other's. No list means none are marked — honest, where a
		// borrowed list would be quietly wrong.
		const [bare = []] = highlightHtml('<td data-if="y">');
		expect(classOf(bare, 'data-if')).toBe(CODE_CLASSES.ATTR);
		// Case-insensitive, since HTML attribute names are and GrapesJS echoes back what it was given.
		const [upper = []] = highlightHtml('<td DATA-IF="y">', ['data-if']);
		expect(classOf(upper, 'DATA-IF')).toBe(CODE_CLASSES.MARKER);
	});

	it('lifts Jinja out of copy AND out of an attribute value', () => {
		// `href="{{ portal_url }}"` is the common case — the part of the attribute an author most wants to pick out.
		const [copy = []] = highlightHtml('<p>Hi {{ first_name }}</p>');
		expect(classOf(copy, '{{ first_name }}')).toBe(CODE_CLASSES.JINJA);
		const [attr = []] = highlightHtml('<a href="{{ portal_url }}">go</a>');
		expect(classOf(attr, '{{ portal_url }}')).toBe(CODE_CLASSES.JINJA);
		expect(text(attr)).toBe('<a href="{{ portal_url }}">go</a>');
	});

	it('does not decorate a half-typed Jinja opener', () => {
		// `jinjaRuns` refuses an opener with no closer, so a template mid-edit isn't swallowed by a chip — reused (not reimplemented) so the code view and canvas overlay can't disagree on what a run is.
		const [line = []] = highlightHtml('<p>{% if x</p>');
		expect(line.some((s) => s.cls === CODE_CLASSES.JINJA)).toBe(false);
		expect(text(line)).toBe('<p>{% if x</p>');
	});

	it('treats a `>` inside an attribute value as part of the value', () => {
		const src = '<div data-if-label="If x is &gt;5" title="a > b">x</div>';
		const [line = []] = highlightHtml(src);
		expect(text(line)).toBe(src);
		expect(classOf(line, '"a > b"')).toBe(CODE_CLASSES.VALUE);
	});

	it('reads a close tag as a tag, not as copy', () => {
		const [line = []] = highlightHtml('<p>hi</p>');
		expect(classOf(line, '</')).toBe(CODE_CLASSES.PUNCT);
		// Both `p`s are the element name, so `find` returns the first — assert the count instead.
		expect(line.filter((s) => s.text === 'p' && s.cls === CODE_CLASSES.NAME)).toHaveLength(2);
	});

	it('leaves a stray `<` in copy as copy', () => {
		const src = '<p>a < b</p>';
		expect(text(highlightHtml(src)[0] ?? [])).toBe(src);
	});

	it('does not read <style> content as markup', () => {
		const src = '<style>a > b { color: red }</style>';
		const [line = []] = highlightHtml(src);
		expect(text(line)).toBe(src);
		expect(line.some((s) => s.text.includes('color: red') && s.cls === CODE_CLASSES.TEXT)).toBe(
			true
		);
	});

	it('reproduces a comment without trying to read inside it', () => {
		const src = '<!-- <td> is not a tag in here -->';
		const [line = []] = highlightHtml(src);
		expect(text(line)).toBe(src);
		expect(classOf(line, src)).toBe(CODE_CLASSES.COMMENT);
	});

	it('answers one empty line for empty input', () => {
		expect(highlightHtml('')).toEqual([[]]);
	});
});
