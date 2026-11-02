import { describe, expect, it } from 'vitest';
import { findMatches, normalise } from './pdf-search';

/**
 * The offsets these return are used to build a DOM Range over the rendered text layer, so an
 * off-by-one is a highlight sitting one character out of place on every hit. That is what most of
 * these pin.
 */

/** The original substring a match points at — the thing the caller will end up highlighting. */
const hit = (page: string, m: { start: number; end: number }) => page.slice(m.start, m.end);

describe('normalise', () => {
	it('maps every output character back to the character that produced it', () => {
		const { text, map } = normalise('Hello');
		expect(text).toBe('hello');
		expect(map).toEqual([0, 1, 2, 3, 4]);
	});

	it('collapses a whitespace run to one space', () => {
		expect(normalise('a  \n\t b').text).toBe('a b');
	});

	it('does not emit a leading space for text that starts with whitespace', () => {
		// Otherwise every offset on the page shifts by one and every highlight is misplaced.
		const { text, map } = normalise('\n\n  Annual');
		expect(text).toBe('annual');
		expect(map[0]).toBe(4);
	});

	it('drops soft hyphens and zero-width characters', () => {
		expect(normalise('re­view').text).toBe('review');
		expect(normalise('re​view').text).toBe('review');
	});
});

describe('findMatches', () => {
	it('finds a plain occurrence and points at the original text', () => {
		const page = 'Your annual review is enclosed.';
		const { matches } = findMatches([page], 'annual review');
		expect(matches).toHaveLength(1);
		expect(hit(page, matches[0])).toBe('annual review');
	});

	it('matches across a line break, because PDF spacing is the layout’s not the author’s', () => {
		const page = 'Your annual\n   review is enclosed.';
		const { matches } = findMatches([page], 'annual review');
		expect(hit(page, matches[0])).toBe('annual\n   review');
	});

	it('finds typeset punctuation from a plainly-typed query', () => {
		const page = 'the client’s objectives';
		expect(hit(page, findMatches([page], "client's").matches[0])).toBe('client’s');
	});

	it('finds an en-dashed range typed with a hyphen', () => {
		const page = 'the 2025–26 tax year';
		expect(hit(page, findMatches([page], '2025-26').matches[0])).toBe('2025–26');
	});

	it('finds a word broken by a soft hyphen', () => {
		const page = 'the annual re­view pack';
		expect(hit(page, findMatches([page], 'review').matches[0])).toBe('re­view');
	});

	it('is case-insensitive', () => {
		expect(findMatches(['SUITABILITY Report'], 'suitability report').matches).toHaveLength(1);
	});

	it('reports matches in reading order, with their page', () => {
		const { matches } = findMatches(['fee here', 'no', 'fee and fee'], 'fee');
		expect(matches.map((m) => m.page)).toEqual([0, 2, 2]);
	});

	it('finds overlapping occurrences', () => {
		expect(findMatches(['aaa'], 'aa').matches).toHaveLength(2);
	});

	it('returns nothing for an empty or whitespace-only query', () => {
		expect(findMatches(['anything'], '').matches).toEqual([]);
		expect(findMatches(['anything'], '   ').matches).toEqual([]);
	});

	it('caps the result set and SAYS it capped it', () => {
		// A one-character query over a long document matches thousands of times. Presenting the first
		// N as if they were all of them is the failure mode this guards.
		const { matches, truncated } = findMatches(['a '.repeat(50)], 'a', 10);
		expect(matches).toHaveLength(10);
		expect(truncated).toBe(true);
	});

	it('does not claim truncation when everything fits', () => {
		expect(findMatches(['a a a'], 'a', 10).truncated).toBe(false);
	});
});
