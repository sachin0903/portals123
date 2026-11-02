/**
 * The firm's content library as the client reads it — the rules the Insights looks and the
 * reader page share, kept pure so they are table-testable.
 *
 * The library is where a firm's identity has the most room: it is the one surface made of the
 * firm's own words and pictures rather than the custodian's numbers. These helpers give the
 * looks their brand levers — a topic's card finish (so a firm with no
 * photography still gets covers in its own palette), the kind's label, a reading time, and the
 * "keep reading" set under an article.
 */
import type { NewsItem } from './types.js';

export type NewsKind = NonNullable<NewsItem['kind']>;

/** How a kind is named on a row ("Guide · 12 Jun 2026") and in the kind switch (plural). */
export const KIND_SINGULAR: Record<NewsKind, string> = {
	news: 'News',
	article: 'Article',
	guide: 'Guide'
};
export const KIND_PLURAL: Record<NewsKind, string> = {
	news: 'News',
	article: 'Articles',
	guide: 'Guides'
};
export const KIND_ORDER: NewsKind[] = ['news', 'article', 'guide'];

export function kindOf(item: Pick<NewsItem, 'kind'>): NewsKind {
	return item.kind ?? 'news';
}

/** "Guide · 12 Jun 2026" — the kind first, since a plain row has no other mark of it. */
export function stampOf(item: Pick<NewsItem, 'kind' | 'time'>): string {
	return [KIND_SINGULAR[kindOf(item)], item.time].filter(Boolean).join(' · ');
}

/**
 * Which slot each topic wears — the topic with the most items takes the brand finish itself,
 * the next the next slot, and so on; ties break on first appearance so the assignment is stable
 * across renders. A sixth topic and beyond wrap round: there are five finishes
 * (`categorical.ts::topicFinish`) and a cover is read with its label beside it, so a shared
 * finish costs less there than it would on an account card.
 */
export function topicSlots(items: readonly Pick<NewsItem, 'source'>[]): Map<string, number> {
	const counts = new Map<string, number>();
	for (const item of items) counts.set(item.source, (counts.get(item.source) ?? 0) + 1);
	const ordered = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([topic]) => topic);
	return new Map(ordered.map((topic, i) => [topic, i]));
}

const WORDS_PER_MINUTE = 220;

/**
 * "4 min read" from the published HTML — the words between the tags, at a reading pace of 220
 * a minute, never below one. Null for an empty body (a PDF guide has none to count), so the
 * header says nothing rather than "1 min read" over a forty-page document.
 */
export function readingTime(html: string | null | undefined): string | null {
	if (!html) return null;
	const text = html
		.replace(/<style[\s\S]*?<\/style>/gi, ' ')
		.replace(/<script[\s\S]*?<\/script>/gi, ' ')
		.replace(/<[^>]+>/g, ' ')
		.replace(/&[a-z#0-9]+;/gi, ' ');
	const words = text.split(/\s+/).filter(Boolean).length;
	if (words === 0) return null;
	return `${Math.max(1, Math.round(words / WORDS_PER_MINUTE))} min read`;
}

/**
 * What to read next: the same topic first (newest first, as the library arrives), then the rest
 * of the library in its order, never the piece being read. `count` of them.
 */
export function relatedInsights<T extends Pick<NewsItem, 'id' | 'source'>>(
	items: readonly T[],
	current: Pick<NewsItem, 'id' | 'source'>,
	count = 3
): T[] {
	const others = items.filter((item) => item.id !== current.id);
	const sameTopic = others.filter((item) => item.source === current.source);
	const rest = others.filter((item) => item.source !== current.source);
	return [...sameTopic, ...rest].slice(0, count);
}
