/**
 * The feed-refetch parse rule. Every case here is about ONE failure mode: the bell's dropdown going
 * empty when it should have kept what it had. An empty dropdown reads as "no notifications", which a
 * user cannot tell apart from a healthy quiet inbox — so a bad answer must leave the rows alone.
 */
import { describe, expect, it } from 'vitest';
import { parseFeedItems } from './feed-refetch.js';

const row = (uid: string) => ({ uid, title: 't', body: null, read_at: null }) as never;

describe('parseFeedItems', () => {
	it('accepts the API shape (a bare array)', () => {
		expect(parseFeedItems([row('a'), row('b')])).toHaveLength(2);
	});

	it("accepts the apps' proxy shape ({items})", () => {
		// The proxies wrap the API's bare list. Both are accepted so neither side has to know which it
		// is talking to, and a proxy that changes its wrapping cannot silently blank the bell.
		expect(parseFeedItems({ items: [row('a')] })).toHaveLength(1);
	});

	it('honours an EMPTY array — "you have nothing" is a real answer', () => {
		// Refusing this would leave dismissed rows on screen for ever.
		expect(parseFeedItems([])).toEqual([]);
		expect(parseFeedItems({ items: [] })).toEqual([]);
	});

	it('returns null for anything unusable, so the caller keeps its rows', () => {
		// null is NOT an empty list. Each of these is a real answer a broken proxy or a changed contract
		// can produce, and none of them may empty the dropdown.
		for (const body of [
			null,
			undefined,
			'',
			'not json',
			0,
			{},
			{ items: null },
			{ items: 'nope' },
			{ count: 3 }, // the unread-count payload, hit by a mis-pointed endpoint
			{ detail: 'Not authenticated' } // an API error body
		]) {
			expect(parseFeedItems(body), JSON.stringify(body ?? String(body))).toBeNull();
		}
	});
});
