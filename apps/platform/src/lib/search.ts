/**
 * The platform portal's header-search POLICY (`$lib/SearchBar.svelte` consumes this).
 *
 * The mechanics — the request-sequence race guard, the debounce rules, the settle step — are the
 * same in every portal and live in `@graphene/ui` (`SearchClient`, `scheduleSearch`, …); this
 * module keeps only what is this portal's own decision (where a hit routes, the keyboard walk
 * over the flattened list) and aliases the shared exports back to their historical local names so
 * the bar and its tests read unchanged.
 */

import {
	BOOK_SEARCH_SCOPES,
	isSearchShortcut as sharedIsSearchShortcut,
	relevanceOrderedSearchHits,
	routableSearchGroups,
	searchCategoriesForGroups,
	searchHighlightParts,
	searchUrl as sharedSearchUrl,
	searchHrefFor,
	type SearchHighlightPart,
	type BookRouteKey,
	type BookSearchScope,
	type SearchGroup,
	type SearchHit,
	type SearchResults,
	type SearchRoutes
} from '@graphene/ui';

export type Hit = SearchHit;
export type Group = SearchGroup;
export type Results = SearchResults;
export type Routes = SearchRoutes;

/** Where each entity type actually lives in this portal — selecting a hit opens THAT record, never
 *  its list page. Any type without a route here is dropped rather than mislinked. `satisfies` pins
 *  the shared firm-book keys; `Routes` is an open map, so the assertion widens onto it. */
export const ROUTES = {
	// Every hit's id is the record's public uid — that is what `SearchHit.id` means (a URL never
	// carries a serial), and clients were the last group returning one until `search.py` was fixed.
	client: (uid) => `/clients/${uid}`,
	account: (uid) => `/accounts/${uid}`,
	document: (uid) => `/documents/${uid}`,
	case: (uid) => `/journeys/${uid}`,
	transaction: (accountUid) => `/accounts/${accountUid}`,
	// `/chats/{uid}` and `/chats/groups/{uid}` are the thread PROXIES the inbox fetches, not pages —
	// a click on one rendered the JSON (or a 404) instead of the conversation. The inbox reads
	// these and opens that thread.
	message: (target) =>
		target.startsWith('group:')
			? `/chats?group=${encodeURIComponent(target.slice(6))}`
			: `/chats?conversation=${encodeURIComponent(target)}`,
	message_group: (uid) => `/chats?group=${encodeURIComponent(uid)}`,
	review: (uid) => `/reviews/${uid}`
} satisfies Record<BookRouteKey, (uid: string) => string> as Routes;

export const SEARCH_SCOPES = BOOK_SEARCH_SCOPES;

export type SearchScope = BookSearchScope;
export type SearchCategory = Exclude<(typeof SEARCH_SCOPES)[number], { type: null }>;

/**
 * The contextual category strip is made from what THIS query actually returned (the shared
 * `searchCategoriesForGroups`); message threads and message groups share one public scope, so
 * they collapse into one chip.
 */
export function categoriesForGroups(groups: readonly Pick<Group, 'type'>[]): SearchCategory[] {
	const categories = SEARCH_SCOPES.filter((s): s is SearchCategory => s.type !== null);
	return searchCategoriesForGroups(groups, categories, (type) =>
		type === 'message_group' ? 'message' : type
	);
}

type PlatformNavigator = {
	platform?: string;
	userAgent?: string;
	userAgentData?: { platform?: string };
};

/**
 * The handler accepts both shortcuts; the chrome should advertise only the one this keyboard uses.
 * `userAgentData.platform` is preferred where Chromium exposes it, with the older platform and UA
 * strings as fallbacks. This stays pure so the browser-only read can happen after hydration.
 */
export function searchShortcutLabel(navigatorLike: PlatformNavigator): '⌘ K' | 'Ctrl K' {
	const platform =
		navigatorLike.userAgentData?.platform ??
		navigatorLike.platform ??
		navigatorLike.userAgent ??
		'';
	return /mac|iphone|ipad|ipod/i.test(platform) ? '⌘ K' : 'Ctrl K';
}

export type HighlightPart = SearchHighlightPart;

/** Safe-to-render highlight runs — the shared `searchHighlightParts`, under this module's name. */
export const highlightParts = searchHighlightParts;

const SEARCH_GUIDE_VERSION = 1;

export function searchGuideStorageKey(userKey: string | null): string {
	return `graphene:platform-search-guide:v${SEARCH_GUIDE_VERSION}:${userKey ?? 'anonymous'}`;
}

/** Marks the guide as seen as soon as it is offered, so closing the palette also counts as the
 * first visit. The caller handles storage being unavailable (private mode or browser policy). */
export function consumeSearchGuide(
	storage: Pick<Storage, 'getItem' | 'setItem'>,
	userKey: string | null
): boolean {
	const key = searchGuideStorageKey(userKey);
	if (storage.getItem(key) === 'seen') return false;
	storage.setItem(key, 'seen');
	return true;
}

/**
 * The two prefixes are deliberately different QUESTIONS, and that is the whole reason they are
 * separate: `/` runs something (it leaves the palette and changes the app), `>` only decides WHERE
 * a search looks. Mixing them put "Create client" and "Search clients" in one list, where the
 * destructive-ish one and the harmless one read identically.
 *
 * `clients:` is the third spelling, for someone who already knows the scope they want and does not
 * want to pick it from a list first — `clients: ashworth` searches clients for "ashworth".
 */
export const COMMAND_PREFIX = '/';
export const SCOPE_PREFIX = '>';

/** What the palette is being asked for. In `search`, `scope` is the scope an inline `clients:`
 *  token named: `undefined` means the query named none, so the palette's own pinned scope stands
 *  (`null` is Everywhere, which a token CAN name). */
export type PaletteQuery =
	| { mode: 'commands'; term: string }
	| { mode: 'scopes'; term: string }
	| { mode: 'search'; term: string; scope?: SearchScope };

/** One place that decides which of the three the text in the box is, so the input's placeholder,
 *  the groups rendered, and what gets sent to the API cannot disagree about it. */
export function parsePaletteQuery(raw: string): PaletteQuery {
	const value = raw.trim();
	if (value.startsWith(COMMAND_PREFIX)) {
		return { mode: 'commands', term: value.slice(1).trim().toLocaleLowerCase() };
	}
	if (value.startsWith(SCOPE_PREFIX)) {
		return { mode: 'scopes', term: value.slice(1).trim().toLocaleLowerCase() };
	}
	const token = /^([a-z]+):([\s\S]*)$/i.exec(value);
	const named = token && SEARCH_SCOPES.find((s) => s.command === token[1].toLocaleLowerCase());
	if (token && named) return { mode: 'search', term: token[2].trim(), scope: named.type };
	return { mode: 'search', term: value };
}

/** The keys the first-run guide prints, built from the prefixes above so a hint cannot advertise a
 *  key the palette no longer answers to. */
export const PALETTE_SHORTCUTS: { keys: string[]; label: string }[] = [
	{ keys: ['⌘/Ctrl', 'K'], label: 'Open search from anywhere' },
	{ keys: [COMMAND_PREFIX], label: 'Quick actions' },
	{ keys: [SCOPE_PREFIX], label: 'Choose a category' },
	{ keys: ['↑', '↓', '↵'], label: 'Move through results and open' },
	{ keys: ['⌫'], label: 'Search all categories again' },
	{ keys: ['esc'], label: 'Go back, then close' }
];

export type PaletteKeyboardAction =
	| { kind: 'toggle' }
	| { kind: 'open'; prefix: typeof COMMAND_PREFIX | typeof SCOPE_PREFIX }
	| { kind: 'clear-scope' };

export function paletteKeyboardAction(
	e: {
		key: string;
		altKey?: boolean;
		metaKey?: boolean;
		ctrlKey?: boolean;
		editable?: boolean;
	},
	state: { open: boolean; query: string; scope: SearchScope }
): PaletteKeyboardAction | null {
	if (sharedIsSearchShortcut(e)) return { kind: 'toggle' };
	// Bare printable keys only ever open the palette from the PAGE, and never while the caller is
	// typing into something — a "/" in a client's name or a note is a character, not a shortcut.
	if (!state.open && !e.altKey && !e.metaKey && !e.ctrlKey && !e.editable) {
		if (e.key === COMMAND_PREFIX) return { kind: 'open', prefix: COMMAND_PREFIX };
		if (e.key === SCOPE_PREFIX) return { kind: 'open', prefix: SCOPE_PREFIX };
	}
	if (!state.open) return null;
	if ((e.key === 'Backspace' || e.key === 'Escape') && !state.query && state.scope) {
		return { kind: 'clear-scope' };
	}
	return null;
}

export function scopedSearchUrl(q: string, scope: SearchScope): string {
	const base = sharedSearchUrl(q);
	const apiScope =
		scope === 'case' ? 'journeys' : scope === 'message' ? 'messages' : scope ? `${scope}s` : null;
	return apiScope ? `${base}&scope=${encodeURIComponent(apiScope)}` : base;
}

/** The API returns typed handles for every group the caller may open; a portal only routes some of
 *  those types. Groups left empty by that filter are dropped, so no heading sits over nothing. */
export function routableGroups(results: Results | null, routes: Routes = ROUTES): Group[] {
	return routableSearchGroups(results, routes);
}

/** Flatten groups into the backend's one cross-entity relevance order (shared). */
export const relevanceOrderedHits = relevanceOrderedSearchHits;

/** Every routable hit in display order — the keyboard walks this, so what Enter opens is exactly
 * what is highlighted. */
export function flatten(results: Results | null, routes: Routes = ROUTES): Hit[] {
	return relevanceOrderedHits(routableGroups(results, routes));
}

/** The path a hit opens, or `null` when this portal has no route for its type. */
export function hrefFor(hit: Hit, routes: Routes = ROUTES): string | null {
	return searchHrefFor(hit, routes);
}

/** Where the palette's "Upload document" command lands, or `null` to withhold it.
 *
 *  On a client's record it opens that client's Documents tab with the upload dialog up: the upload
 *  is then for the client on screen, and the tab needs only `documents:read`, which the write
 *  implies. Anywhere else it opens the firm-wide Documents page, which needs `documents:show` too —
 *  without that there is nowhere to land, so the command is withheld rather than a 403. */
export function uploadDocumentHref(
	routeId: string | null,
	clientUid: string | undefined,
	allowed: (slug: string) => boolean
): string | null {
	if (!allowed('documents:write')) return null;
	// Route groups (`/(app)/…`) are not part of the path, so they must not defeat the match.
	const route = routeId?.replace(/\/\([^)]+\)/g, '') ?? '';
	if (clientUid && (route === '/clients/[uid]' || route.startsWith('/clients/[uid]/'))) {
		return `/clients/${clientUid}/documents?upload=1`;
	}
	return allowed('documents:show') ? '/documents?upload=1' : null;
}

/** Where ArrowUp/ArrowDown moves the highlight (wrapping), or `null` when the key isn't a move or
 *  there is nothing to move over — the caller then leaves the event alone. */
export function moveActive(key: string, active: number, length: number): number | null {
	if (length === 0) return null;
	if (key === 'ArrowDown') return (active + 1) % length;
	if (key === 'ArrowUp') return (active - 1 + length) % length;
	return null;
}

// The shared mechanics, under this module's historical names.
export {
	scheduleSearch as scheduleQuery,
	SEARCH_DEBOUNCE_MS as DEBOUNCE_MS,
	SEARCH_MIN_QUERY_LENGTH as MIN_QUERY_LENGTH,
	SearchClient,
	searchUrl,
	isSearchShortcut,
	settleSearch as settle,
	type SearchOutcome as Outcome,
	type SearchQuerySink as QuerySink
} from '@graphene/ui';
