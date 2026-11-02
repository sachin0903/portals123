import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { BrandingBlock } from '@graphene/auth';
import { personaByKey } from '../../fixtures/personas.js';
import NewsFeed from './news-feed.svelte';
import NewsLibrary from './news-library.svelte';
import PortalPage from './portal-page.svelte';
import type { NewsItem } from './types.js';

/**
 * The news block's look axis (Oct 2026): the library and the home feed each wear `card`,
 * `magazine` or `journal`. SSR smoke, in the portal-page.ssr idiom — that each look reaches its
 * component, that a piece without a hero gets a cover in the firm's palette rather than a blank,
 * and that the ledger finish's auto resolves to the journal.
 */

const items: NewsItem[] = [
	{
		id: 'lead',
		source: 'Markets',
		headline: 'Gilt yields steady as investors weigh the next move',
		summary: 'Bond markets held a narrow range after fresh inflation data.',
		time: '12 Jun 2026',
		href: '/insights/lead',
		kind: 'article',
		heroUrl: 'https://img.example/lead.jpg'
	},
	{
		id: 'second',
		source: 'Planning',
		headline: 'New ISA year prompts a look at allowances',
		summary: '',
		time: '10 Jun 2026',
		href: '/insights/second',
		kind: 'guide'
	},
	{
		id: 'third',
		source: 'Markets',
		headline: 'Global equities edge higher',
		summary: '',
		time: '8 Jun 2026',
		href: '/insights/third',
		kind: 'news'
	}
];

const library = (look?: string) =>
	render(NewsLibrary, { props: { items, look, interactive: false } }).body;
const feed = (look?: string) =>
	render(NewsFeed, { props: { items, look, interactive: false } }).body;

describe('NewsLibrary looks', () => {
	it('card is the reading list: a lead with its hero, the rest as rows', () => {
		const body = library('card');
		expect(body).toContain('data-look="card"');
		expect(body).toContain('data-news-lead');
		expect(body).toContain('src="https://img.example/lead.jpg"');
		// Rows, not cards: no cover drawn for the pieces without a hero.
		expect(body).not.toContain('data-insight-cover');
		expect(body).toContain('Guide · 10 Jun 2026');
	});

	it('magazine is a cover story and a grid of covers — the palette stands in for a missing hero', () => {
		const body = library('magazine');
		expect(body).toContain('data-look="magazine"');
		expect(body).toContain('data-news-lead');
		expect(body).toContain('src="https://img.example/lead.jpg"');
		expect((body.match(/data-news-card/g) ?? []).length).toBe(2);
		// Two pieces without a hero → two typographic covers, each in the topic's face.
		expect((body.match(/data-insight-cover/g) ?? []).length).toBe(2);
		expect(body).toContain('oklch(from var(--brand)');
		// The cover carries the topic's initial in the display face.
		expect(body).toMatch(/>\s*P\s*<\/p>/);
	});

	it('journal is type-led and chrome-less: no covers, no cards, a brand rule', () => {
		const body = library('journal');
		expect(body).toContain('data-look="journal"');
		expect(body).not.toContain('data-insight-cover');
		expect(body).not.toContain('<img');
		expect(body).not.toContain('glass-panel');
		expect(body).toContain('border-brand');
		expect(body).toContain('var(--font-display');
	});

	it('an unknown look renders the card list', () => {
		expect(library('shelf')).toContain('data-news-lead');
		expect(library('shelf')).not.toContain('data-insight-cover');
	});

	it('every look keeps the library’s own filters and empty states', () => {
		for (const look of ['card', 'magazine', 'journal']) {
			// A small library has no topic tabs or search yet, so its kinds take the tab row.
			const body = library(look);
			expect(body).toContain('aria-label="Filter by type"');
			expect(body).not.toContain('aria-label="Search insights"');
			expect(render(NewsLibrary, { props: { items: [], look } }).body).toContain(
				'Nothing to read yet'
			);
		}
	});

	it('a large library tabs by topic, with the kind as a pull-down and search as a button', () => {
		const many: NewsItem[] = Array.from({ length: 12 }, (_, n) => ({
			...items[n % items.length],
			id: `item-${n}`,
			href: `/insights/item-${n}`
		}));
		const body = render(NewsLibrary, { props: { items: many, look: 'magazine' } }).body;
		expect(body).toContain('aria-label="Filter by topic"');
		expect(body).toMatch(/aria-pressed="true"[^>]*>\s*All\s*</);
		expect(body).toContain('>Markets<');
		expect(body).toContain('aria-label="Show: All types"');
		expect(body).toContain('aria-label="Search insights"');
		// Search is closed until asked for: no field in the server render.
		expect(body).not.toContain('type="search"');
	});
});

describe('NewsFeed looks', () => {
	it('card is the headline list in a card', () => {
		const body = feed('card');
		expect(body).toContain('data-look="card"');
		expect(body).toContain('data-slot="card"');
		expect(body).not.toContain('data-news-lead');
	});

	it('magazine leads with the newest piece on a cover, the rest as headlines', () => {
		const body = feed('magazine');
		expect(body).toContain('data-look="magazine"');
		expect(body).toContain('data-news-lead');
		expect(body).toContain('src="https://img.example/lead.jpg"');
		expect(body).toContain('New ISA year prompts a look at allowances');
	});

	it('journal has no card chrome', () => {
		const body = feed('journal');
		expect(body).toContain('data-look="journal"');
		expect(body).not.toContain('data-slot="card"');
		expect(body).toContain('Market news');
	});
});

describe('PortalPage routes the news look', () => {
	const trust = personaByKey('trust300');
	const html = (blocks: BrandingBlock[], props: Record<string, unknown> = {}) =>
		render(PortalPage, {
			props: { blocks, data: { ...trust.data, news: items }, interactive: false, ...props }
		}).body;

	it('an explicit look reaches the library and the feed', () => {
		expect(html([{ type: 'news', settings: { variant: 'library', look: 'magazine' } }])).toContain(
			'data-look="magazine"'
		);
		expect(html([{ type: 'news', settings: { variant: 'feed', look: 'journal' } }])).toContain(
			'data-look="journal"'
		);
	});

	it('auto follows the finish: card by default, journal under the ledger', () => {
		expect(html([{ type: 'news', settings: { variant: 'library' } }])).toContain(
			'data-look="card"'
		);
		expect(
			html([{ type: 'news', settings: { variant: 'library' } }], { finish: 'ledger' })
		).toContain('data-look="journal"');
	});
});
