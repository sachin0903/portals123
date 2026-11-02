/**
 * The operations header search POLICY (`$lib/SearchBar.svelte` consumes this).
 *
 * Two modes:
 * - **Firms** (global): flatten `/search/tenants` hits → `/tenants/{uid}`.
 * - **Firm book** (under `/tenants/[uid]/…`): platform-parity palette over `/search?tenant=`,
 *   hits → `/tenants/{uid}/operate/…`. Entity keys are `BOOK_ROUTE_KEYS` from `@graphene/ui`.
 */

import { can } from '$lib/permissions';
import {
	BOOK_SEARCH_SCOPES,
	isSearchShortcut as sharedIsSearchShortcut,
	routableSearchGroups,
	searchUrl as sharedSearchUrl,
	searchHrefFor,
	type BookRouteKey,
	type BookSearchScope,
	type SearchGroup,
	type SearchHit,
	type SearchOutcome,
	type SearchResults,
	type SearchRoutes
} from '@graphene/ui';

export type Hit = SearchHit;
export type Group = SearchGroup;
export type Results = SearchResults;
export type Outcome = SearchOutcome;
export type Routes = SearchRoutes;

/** The ops bar shows one flat list of firms, so the API's groups are flattened in order. */
export function firms(results: Results | null): Hit[] {
	return (results?.groups ?? []).flatMap((g) => g.hits);
}

/** Where a firm hit opens. The hit's `id` is the firm's UID (search.py selects `t.uid`),
 *  because that is what the tenant routes are addressed by. */
export const firmHref = (uid: string): string => `/tenants/${uid}`;

/** The one permission the firm search needs, in one place so the three enforcement points can't
 *  drift: `src/routes/search/+server.ts` (this portal's proxy), `search.py::search_tenants`
 *  (`require_permission(TENANTS_READ)`) and the bar itself. */
export const SEARCH_PERMISSION = 'tenants:read';

/** In-firm book search — same workspace gate as operate loaders. */
export const FIRM_BOOK_SEARCH_PERMISSION = 'tenant_portal:read';

/** Should this operations user be offered the global firms search bar? */
export function canSearch(
	permissions: Iterable<string> | null | undefined,
	role: string | null
): boolean {
	return can(permissions ?? [], role, SEARCH_PERMISSION);
}

/** Should this operations user be offered the in-firm book palette? */
export function canSearchFirmBook(
	permissions: Iterable<string> | null | undefined,
	role: string | null
): boolean {
	return can(permissions ?? [], role, FIRM_BOOK_SEARCH_PERMISSION);
}

/** What one settled firms query does to the palette. `null` means the query was superseded. */
export function settle(outcome: Outcome | null): { firms: Hit[]; failed: boolean } | null {
	if (outcome === null) return null;
	return outcome.ok
		? { firms: firms(outcome.results), failed: false }
		: { firms: [], failed: true };
}

/**
 * Operate-path routes for one firm — the shared firm-book keys, under this tenant.
 * `satisfies` pins those keys; `Routes` is an open map, so the assertion widens onto it.
 */
export function firmBookRoutes(tenantUid: string): Routes {
	const base = `/tenants/${tenantUid}/operate`;
	return {
		client: (uid) => `${base}/clients/${uid}`,
		account: (uid) => `${base}/accounts/${uid}`,
		document: (uid) => `${base}/documents/${uid}`,
		case: (uid) => `${base}/journeys/${uid}`,
		transaction: (accountUid) => `${base}/accounts/${accountUid}`,
		message: (target) =>
			target.startsWith('group:') ? `${base}/messages` : `${base}/clients/${target}/chat`,
		message_group: () => `${base}/messages`,
		review: (uid) => `${base}/reviews?open=${encodeURIComponent(uid)}`
	} satisfies Record<BookRouteKey, (id: string) => string> as Routes;
}

export const SEARCH_SCOPES = BOOK_SEARCH_SCOPES;

export type SearchScope = BookSearchScope;
export type SearchCategory = Exclude<(typeof SEARCH_SCOPES)[number], { type: null }>;

export function categoriesForGroups(groups: readonly Pick<Group, 'type'>[]): SearchCategory[] {
	const returned = new Set(
		groups.map((group) => (group.type === 'message_group' ? 'message' : group.type))
	);
	const categories: SearchCategory[] = [];
	for (const item of SEARCH_SCOPES) {
		if (item.type !== null && returned.has(item.type)) categories.push(item);
	}
	return categories;
}

export const COMMAND_PREFIX = '/' as const;
export const SCOPE_PREFIX = '>' as const;

export type PaletteQuery =
	| { mode: 'commands'; term: string }
	| { mode: 'scopes'; term: string }
	| { mode: 'search'; term: string; scope?: SearchScope };

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

export function firmBookSearchUrl(tenantUid: string, q: string, scope: SearchScope): string {
	const params = new URLSearchParams({ q });
	const apiScope =
		scope === 'case' ? 'journeys' : scope === 'message' ? 'messages' : scope ? `${scope}s` : null;
	if (apiScope) params.set('scope', apiScope);
	return `/tenants/${tenantUid}/search?${params}`;
}

export function routableGroups(results: Results | null, routes: Routes): Group[] {
	return routableSearchGroups(results, routes);
}

export function relevanceOrderedHits(groups: readonly Group[]): Hit[] {
	return groups
		.flatMap((group) => group.hits)
		.map((hit, index) => ({ hit, index }))
		.sort((a, b) => {
			const left = a.hit.rank;
			const right = b.hit.rank;
			if (left == null && right == null) return a.index - b.index;
			if (left == null) return 1;
			if (right == null) return -1;
			return left - right || a.index - b.index;
		})
		.map(({ hit }) => hit);
}

export function hrefFor(hit: Hit, routes: Routes): string | null {
	return searchHrefFor(hit, routes);
}

type PlatformNavigator = {
	platform?: string;
	userAgent?: string;
	userAgentData?: { platform?: string };
};

export function searchShortcutLabel(navigatorLike: PlatformNavigator): '⌘ K' | 'Ctrl K' {
	const platform =
		navigatorLike.userAgentData?.platform ??
		navigatorLike.platform ??
		navigatorLike.userAgent ??
		'';
	return /mac|iphone|ipad|ipod/i.test(platform) ? '⌘ K' : 'Ctrl K';
}

export type HighlightPart = { text: string; matched: boolean };

export function highlightParts(value: string, query: string): HighlightPart[] {
	const folded: string[] = [];
	const starts: number[] = [];
	const ends: number[] = [];
	for (let offset = 0; offset < value.length;) {
		const point = value.codePointAt(offset);
		if (point === undefined) break;
		const character = String.fromCodePoint(point);
		const end = offset + character.length;
		const normalised = character.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase();
		for (let index = 0; index < normalised.length; index += 1) {
			folded.push(normalised[index] ?? '');
			starts.push(offset);
			ends.push(end);
		}
		offset = end;
	}
	const needle = query.trim().normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase();
	if (!needle || folded.length === 0) return [{ text: value, matched: false }];

	const hay = folded.join('');
	const parts: HighlightPart[] = [];
	let cursor = 0;
	let from = 0;
	while (from < hay.length) {
		const at = hay.indexOf(needle, from);
		if (at < 0) break;
		const matchStart = starts[at] ?? 0;
		const matchEnd = ends[at + needle.length - 1] ?? value.length;
		if (matchStart > cursor) {
			parts.push({ text: value.slice(cursor, matchStart), matched: false });
		}
		parts.push({ text: value.slice(matchStart, matchEnd), matched: true });
		cursor = matchEnd;
		from = at + needle.length;
	}
	if (cursor < value.length) parts.push({ text: value.slice(cursor), matched: false });
	return parts.length ? parts : [{ text: value, matched: false }];
}

const GUIDE_KEY = 'graphene.admin.firm-search-guide';

export function consumeSearchGuide(
	storage: { getItem(key: string): string | null; setItem(key: string, value: string): void },
	userKey: string | null
): boolean {
	const key = userKey ? `${GUIDE_KEY}:${userKey}` : GUIDE_KEY;
	if (storage.getItem(key) === '1') return false;
	storage.setItem(key, '1');
	return true;
}

// The shared mechanics, under this module's historical names.
export {
	scheduleSearch as scheduleQuery,
	SEARCH_DEBOUNCE_MS as DEBOUNCE_MS,
	SEARCH_MIN_QUERY_LENGTH as MIN_QUERY_LENGTH,
	SearchClient,
	searchUrl,
	settleSearch as settleBook,
	type SearchQuerySink as QuerySink
} from '@graphene/ui';

// Re-export for callers that need the raw shared URL builder.
export { sharedSearchUrl };
