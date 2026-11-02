import { describe, expect, it } from 'vitest';
import { humanise, sentenceCase } from './sentence-case';

describe('sentenceCase', () => {
	it('turns every key shape in the estate into words', () => {
		expect(sentenceCase('execution_only')).toBe('Execution only');
		expect(sentenceCase('document.sent')).toBe('Document sent');
		expect(sentenceCase('morgan-lloyd')).toBe('Morgan lloyd');
	});

	it('is SENTENCE case, not title case', () => {
		// The habit it replaces is `class="capitalize"`, which uppercases every word — so a
		// two-word value came out "Bank Transfer" beside a one-word neighbour in sentence case.
		expect(sentenceCase('not_a_contribution')).toBe('Not a contribution');
		expect(sentenceCase('bank transfer')).toBe('Bank transfer');
	});

	it('leaves an acronym alone', () => {
		// Only the first character is touched. Lower-casing the tail would turn every wrapper in
		// the book into "Gia" / "Isa" / "Sipp".
		expect(sentenceCase('GIA')).toBe('GIA');
		expect(sentenceCase('SIPP')).toBe('SIPP');
		expect(sentenceCase('AML review')).toBe('AML review');
	});

	it('returns the value it was given when there are no words in it', () => {
		expect(sentenceCase('')).toBe('');
		expect(sentenceCase('___')).toBe('___');
	});
});

describe('humanise', () => {
	it('is the same namer with the book’s empty cell', () => {
		expect(humanise('execution_only')).toBe('Execution only');
		expect(humanise(null)).toBe('—');
		expect(humanise(undefined)).toBe('—');
		expect(humanise('')).toBe('—');
	});
});
