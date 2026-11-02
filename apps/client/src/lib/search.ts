/**
 * The client portal's search POLICY (`$lib/SearchBar.svelte` consumes this).
 *
 * The mechanics — the request-sequence race guard, the debounce rules, the settle step — are the
 * same in every portal and live in `@graphene/ui` (`SearchClient`, `scheduleSearch`, …); this
 * module keeps only what is this portal's own decision (where a hit routes) and aliases the shared
 * exports back to their historical local names so the bar and its tests read unchanged.
 */

import {
	routableSearchGroups,
	searchHrefFor,
	type SearchCategory,
	type SearchGroup,
	type SearchHit,
	type SearchResults,
	type SearchRoutes
} from '@graphene/ui';

export type Hit = SearchHit;
export type Group = SearchGroup;
export type Results = SearchResults;
export type Routes = SearchRoutes;

/** Where each entity type lives in this portal. Holdings and transactions target their owning
 * account because neither has a client-facing record route of its own; the hit keeps its own id
 * separately, so two matches on one account remain two selectable results. Action items are not
 * searched — they are worked from home, a handful at a time — so the API's `task` group has no
 * route here and is dropped. Types absent here are dropped rather than mislinked. */
export const ROUTES: Routes = {
	account: (uid) => `/accounts/${uid}`,
	document: (id) => `/documents/${id}`,
	holding: (accountUid) => `/accounts/${accountUid}`,
	insight: (uid) => `/insights/${uid}`,
	transaction: (accountUid) => `/accounts/${accountUid}`
};

/** The categories a client can search, in the palette's display order. The API group types are
 *  the keys; the labels are the client's words for them. */
export const SEARCH_SCOPES = [
	{ type: 'account', label: 'Accounts' },
	{ type: 'holding', label: 'Holdings' },
	{ type: 'transaction', label: 'Transactions' },
	{ type: 'document', label: 'Documents' },
	{ type: 'insight', label: 'Insights' }
] as const satisfies readonly SearchCategory[];

/** Groups with the unroutable hits removed, and any group left empty by that dropped — so no
 *  heading ever sits over nothing. */
export function routableGroups(results: Results | null, routes: Routes = ROUTES): Group[] {
	return routableSearchGroups(results, routes);
}

/** The path a hit opens, or `null` when this portal has no route for its type. */
export function hrefFor(hit: Hit, routes: Routes = ROUTES): string | null {
	return searchHrefFor(hit, routes);
}

// The shared mechanics, under this module's historical names.
export {
	isSearchShortcut as isPaletteShortcut,
	scheduleSearch as scheduleQuery,
	SEARCH_DEBOUNCE_MS as DEBOUNCE_MS,
	SEARCH_MIN_QUERY_LENGTH as MIN_QUERY_LENGTH,
	SearchClient,
	searchUrl,
	settleSearch as settle,
	type SearchOutcome as Outcome,
	type SearchQuerySink as QuerySink
} from '@graphene/ui';
