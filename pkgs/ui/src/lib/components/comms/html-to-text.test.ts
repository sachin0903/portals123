import { describe, expect, it } from 'vitest';
import { htmlToPlainText } from './html-to-text.js';

describe('htmlToPlainText', () => {
	it('turns a table email into readable lines, not one concatenated string', () => {
		const html =
			"<table><tr><td><h1>Set your password</h1><p>Hi there,</p><p>You've been invited.</p></td></tr></table>";
		expect(htmlToPlainText(html)).toBe("Set your password\nHi there,\nYou've been invited.");
	});

	it('drops script and style rather than leaking them as text', () => {
		expect(htmlToPlainText('<p>Hi</p><script>alert(1)</script><style>p{color:red}</style>')).toBe(
			'Hi'
		);
	});

	it('decodes the entities an email body actually uses', () => {
		expect(htmlToPlainText('<p>A &amp; B &lt; C&nbsp;D</p>')).toBe('A & B < C D');
	});

	it('answers empty for empty', () => {
		expect(htmlToPlainText('')).toBe('');
	});
});
