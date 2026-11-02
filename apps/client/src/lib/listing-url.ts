/**
 * URL round-trip for the composed listing pages' toolbars (PortalPage's `listingQuery` /
 * `onlistingchange`). The app owns the toolbar state via the URL — `?q=` and `?account=` — so a
 * filtered listing is shareable, refreshable and back-button-friendly, and the server loader
 * re-runs with the query (page-data.ts threads it into the dedicated listing fetches). Pure and
 * framework-free — no `$app` imports — so both the server loaders and composed-page.svelte use
 * the SAME parse/build rules and they cannot drift apart (listing-url.test.ts).
 */
import type { ListingQuery } from '@graphene/ui';

/** The longest search term the API honours (crm.py's `_SEARCH_Q_MAX` clamps, not rejects);
 * parsing AND building both cap here so the URL never carries a tail the server would ignore —
 * and so what the toolbar shows always equals what was actually searched. */
const Q_MAX = 80;

/** Junk — null, blank, whitespace — reads as "unfiltered" (absent key); over-long truncates.
 * Same clamp-don't-error rule as clampPage: a mangled link degrades, never 400s. */
function normalise(value: string | null | undefined): string | undefined {
	const v = (value ?? '').trim().slice(0, Q_MAX);
	return v || undefined;
}

/** Parse `?q=` / `?account=` off a page URL into PortalPage's listingQuery shape. */
export function listingQueryOf(url: URL): ListingQuery {
	return {
		q: normalise(url.searchParams.get('q')),
		account: normalise(url.searchParams.get('account'))
	};
}

/**
 * The href a toolbar edit navigates to: the same path with `q`/`account` updated — empties
 * DROPPED (a cleared search leaves no `?q=` husk), every unrelated param preserved — and
 * `?page=` REMOVED: a new query invalidates the old page number (page 3 of the unfiltered book
 * is not page 3 of the matches), and to clampPage an absent param IS page 1.
 */
export function listingHref(url: URL, next: ListingQuery): string {
	const params = new URLSearchParams(url.searchParams);
	for (const [key, value] of [
		['q', normalise(next.q)],
		['account', normalise(next.account)]
	] as const) {
		if (value) params.set(key, value);
		else params.delete(key);
	}
	params.delete('page');
	const qs = params.toString();
	return qs ? `${url.pathname}?${qs}` : url.pathname;
}
