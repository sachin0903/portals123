/**
 * Where a card's "View all" may point — decided from the firm's OWN page set, never assumed.
 *
 * The listing cards (accounts, holdings, activity, documents) each used to link to the built-in
 * slug of the same name, unconditionally. The portal's pages are firm-defined: a firm may delete
 * its Holdings page, rename it, or fold holdings into a page called "Portfolio". Every one of
 * those left a "View all" on the home card pointing at `/holdings`, which the listing route
 * answers with a 404 — the client saw "Something went wrong" from a button the portal itself
 * drew. Here a destination exists only when some OTHER page in the effective set shows a visible
 * block of that source; the built-in slug is preferred when it is among them. The default set
 * has no accounts page and puts holdings on Portfolio and the library on Insights, so that is
 * where an uncustomised tenant's cards point.
 *
 * Pure and svelte-free so the client portal's server loader and the studio's preview compute the
 * same answer from the same pages — the preview shows the firm the link they will and will not get.
 */
import type { BrandingPage } from '@graphene/auth';
import { layoutFor, type Audience } from './page-audience.js';

export type ListingSource = 'accounts' | 'holdings' | 'activity' | 'documents' | 'news';
export const LISTING_SOURCES: readonly ListingSource[] = [
	'accounts',
	'holdings',
	'activity',
	'documents',
	'news'
];

/** Per source, the path of the page that lists it, or null when the firm's set has none. */
export type ListingLinks = Record<ListingSource, string | null>;

/** The path a page is served at: home is the portal's root, everything else its slug. */
export function pagePath(slug: string): string {
	return slug === 'home' ? '/' : `/${slug}`;
}

function hosts(page: BrandingPage, source: ListingSource, audience: Audience): boolean {
	return layoutFor(page, audience).some((b) => b.type === source && b.visible !== false);
}

/**
 * The "View all" destinations for the page at `currentSlug`, for one audience.
 *
 * A page never links to itself (the card IS the listing there); home is never a destination (it
 * is the overview — its cards are the teasers "View all" leads AWAY from, and a listing page
 * pointing back at a five-row home card is a link to less); and a page the audience cannot see
 * the block on does not count — an organisation whose Holdings page was emptied for trusts gets
 * no link to it. Among the pages that qualify, the built-in slug wins, then page order.
 */
export function listingLinks(
	pages: readonly BrandingPage[],
	audience: Audience,
	currentSlug: string
): ListingLinks {
	const others = pages.filter((p) => p.slug !== currentSlug && p.slug !== 'home');
	const links = {} as ListingLinks;
	for (const source of LISTING_SOURCES) {
		const hosting = others.filter((p) => hosts(p, source, audience));
		const target = hosting.find((p) => p.slug === source) ?? hosting[0];
		links[source] = target ? pagePath(target.slug) : null;
	}
	return links;
}
