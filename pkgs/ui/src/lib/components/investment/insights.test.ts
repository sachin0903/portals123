import { describe, expect, it } from 'vitest';
import { readingTime, relatedInsights, stampOf, topicSlots } from './insights.js';
import type { NewsItem } from './types.js';

const item = (id: string, source: string, over: Partial<NewsItem> = {}): NewsItem => ({
	id,
	source,
	headline: id,
	summary: '',
	time: '12 Jun 2026',
	href: `/insights/${id}`,
	...over
});

describe('topicSlots', () => {
	it('gives the busiest topic the brand hue (slot 0) and the rest the following slots', () => {
		const slots = topicSlots([
			item('a', 'Planning'),
			item('b', 'Markets'),
			item('c', 'Markets'),
			item('d', 'Tax')
		]);
		expect(slots.get('Markets')).toBe(0);
		expect(slots.get('Planning')).toBe(1);
		expect(slots.get('Tax')).toBe(2);
	});

	it('breaks a tie on first appearance, so the assignment is stable', () => {
		const slots = topicSlots([item('a', 'Planning'), item('b', 'Markets')]);
		expect(slots.get('Planning')).toBe(0);
		expect(slots.get('Markets')).toBe(1);
	});
});

describe('stampOf', () => {
	it('leads with the kind and drops a missing date', () => {
		expect(stampOf({ kind: 'guide', time: '12 Jun 2026' })).toBe('Guide · 12 Jun 2026');
		expect(stampOf({ time: '' })).toBe('News');
	});
});

describe('readingTime', () => {
	it('counts the words between the tags at 220 a minute, never below one', () => {
		const words = Array.from({ length: 880 }, (_, i) => `w${i}`).join(' ');
		expect(readingTime(`<div class="article-shell"><p>${words}</p></div>`)).toBe('4 min read');
		expect(readingTime('<p>Short.</p>')).toBe('1 min read');
	});

	it('ignores styles, scripts and entities', () => {
		expect(readingTime('<style>p{color:red}</style><p>One&nbsp;two</p>')).toBe('1 min read');
	});

	it('is null for no body — a PDF guide is not "1 min read"', () => {
		expect(readingTime(null)).toBeNull();
		expect(readingTime('')).toBeNull();
		expect(readingTime('<div></div>')).toBeNull();
	});
});

describe('relatedInsights', () => {
	const library = [
		item('a', 'Markets'),
		item('b', 'Planning'),
		item('c', 'Markets'),
		item('d', 'Tax'),
		item('e', 'Markets')
	];

	it('offers the same topic first, then the rest of the library, never the piece itself', () => {
		expect(relatedInsights(library, item('c', 'Markets')).map((i) => i.id)).toEqual([
			'a',
			'e',
			'b'
		]);
	});

	it('is as many as asked for, and fewer when the library is small', () => {
		expect(relatedInsights(library, item('a', 'Markets'), 2).map((i) => i.id)).toEqual(['c', 'e']);
		expect(relatedInsights([item('a', 'Markets')], item('a', 'Markets'))).toEqual([]);
	});
});
