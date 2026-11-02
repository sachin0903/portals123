import { describe, expect, it } from 'vitest';
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import { listingLinks, pagePath } from './listing-links.js';
import { defaultPages } from './portal-pages.js';

const block = (type: string, visible = true): BrandingBlock => ({
	id: type,
	type,
	visible,
	w: 6,
	h: 2,
	settings: {}
});

const page = (
	slug: string,
	blocks: BrandingBlock[],
	layouts: Partial<BrandingPage['layouts']> = {}
): BrandingPage => ({
	id: `pg_${slug}`,
	slug,
	label: slug,
	icon: 'layout-grid',
	nav: true,
	layouts: { default: blocks, ...layouts }
});

describe('listingLinks', () => {
	it('an uncustomised tenant links at the default pages: portfolio for holdings, insights for news', () => {
		const links = listingLinks(defaultPages(), 'individual', 'home');
		expect(links).toEqual({
			accounts: null,
			holdings: '/portfolio',
			activity: '/activity',
			documents: '/documents',
			news: '/insights'
		});
	});

	it('a page never links to itself', () => {
		const links = listingLinks(defaultPages(), 'individual', 'portfolio');
		expect(links.holdings).toBeNull();
		expect(links.activity).toBe('/activity');
	});

	// The bug this exists for: the firm removed its Holdings/Activity/Documents pages and every
	// home card still pointed at the built-in slug, which the route 404s.
	it('a source no other page shows has no destination', () => {
		const pages = [
			page('home', [block('portfolioHeader'), block('holdings'), block('activity')]),
			page('accounts', [block('accounts')])
		];
		const links = listingLinks(pages, 'individual', 'home');
		expect(links).toEqual({
			accounts: '/accounts',
			holdings: null,
			activity: null,
			documents: null,
			news: null
		});
	});

	it('follows a renamed page — the firm folded holdings into "portfolio"', () => {
		const pages = [
			page('home', [block('holdings')]),
			page('portfolio', [block('holdings'), block('activity')])
		];
		expect(listingLinks(pages, 'individual', 'home').holdings).toBe('/portfolio');
		expect(listingLinks(pages, 'individual', 'home').activity).toBe('/portfolio');
	});

	it('prefers the built-in slug when several pages show the source', () => {
		const pages = [
			page('home', [block('holdings')]),
			page('portfolio', [block('holdings')]),
			page('holdings', [block('holdings')])
		];
		expect(listingLinks(pages, 'individual', 'home').holdings).toBe('/holdings');
	});

	it('a hidden block does not count as a destination', () => {
		const pages = [
			page('home', [block('documents')]),
			page('documents', [block('documents', false)])
		];
		expect(listingLinks(pages, 'individual', 'home').documents).toBeNull();
	});

	it('judges the page by the AUDIENCE’s layout', () => {
		// Holdings page emptied for organisations: a trust gets no link, an individual does.
		const pages = [
			page('home', [block('holdings')]),
			page('holdings', [block('holdings')], { organisation: [] })
		];
		expect(listingLinks(pages, 'individual', 'home').holdings).toBe('/holdings');
		expect(listingLinks(pages, 'organisation', 'home').holdings).toBeNull();
	});

	it('home is never a destination — a listing must not "View all" back to a teaser', () => {
		const pages = [page('home', [block('holdings')]), page('holdings', [block('holdings')])];
		expect(listingLinks(pages, 'individual', 'holdings').holdings).toBeNull();
		expect(pagePath('home')).toBe('/');
		expect(pagePath('papers')).toBe('/papers');
	});
});
