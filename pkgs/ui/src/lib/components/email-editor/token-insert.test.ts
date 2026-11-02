import { describe, expect, it } from 'vitest';
import { insertAtCaret } from './token-insert';

const TOKEN = '{{ brand.name }}';

describe('insertAtCaret', () => {
	it('inserts at the caret and reports where the caret belongs after', () => {
		const { value, caret } = insertAtCaret('Your  valuation', TOKEN, 5, 5);
		expect(value).toBe('Your {{ brand.name }} valuation');
		expect(caret).toBe(5 + TOKEN.length);
		expect(value.slice(caret)).toBe(' valuation');
	});

	it('replaces a selection, so an author can swap a word for a field', () => {
		expect(insertAtCaret('Your firm valuation', TOKEN, 5, 9).value).toBe(
			'Your {{ brand.name }} valuation'
		);
	});

	it('tolerates a backwards selection, which a shift-drag leftwards produces', () => {
		expect(insertAtCaret('Your firm valuation', TOKEN, 9, 5).value).toBe(
			'Your {{ brand.name }} valuation'
		);
	});

	it('appends when the caret is unknown — a field never focused has none', () => {
		// The end of the line is where someone who has not placed a caret is looking; inserting at 0
		// instead would put the token in front of a subject they had already written.
		for (const caret of [null, undefined, -1, Number.NaN, 999]) {
			expect(insertAtCaret('Your valuation', TOKEN, caret, caret).value).toBe(
				`Your valuation${TOKEN}`
			);
		}
	});

	it('adds no padding of its own', () => {
		// A space here cannot be seen in an inbox and is inherited by every subject the firm sends.
		expect(insertAtCaret('', TOKEN, 0, 0).value).toBe(TOKEN);
		expect(insertAtCaret('ab', TOKEN, 1, 1).value).toBe(`a${TOKEN}b`);
	});

	it('treats a null value as empty rather than throwing', () => {
		expect(insertAtCaret(null as unknown as string, TOKEN, 0, 0).value).toBe(TOKEN);
	});
});
