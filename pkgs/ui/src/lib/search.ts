/**
 * The portal-search mechanics behind every ⌘K bar (client, platform and admin each had a verbatim
 * copy of these in their `$lib/search.ts` — the UI-consolidation sweep hoisted the shared half
 * here). What lives here is everything that is the SAME in every portal and can silently go
 * wrong: the request-sequence race guard, the debounce rules, the settle step and the
 * "drop hits this portal can't route" rule.
 *
 * What deliberately does NOT live here is portal policy: each app's `$lib/search.ts` keeps its own
 * `ROUTES` map (where a hit opens — the client portal must never route a staff-only "Clients" hit),
 * its permission gating (admin's `canSearch`) and its result shaping (admin flattens to firms).
 * The apps alias these exports back to their historical local names, so their search bars and
 * tests read unchanged.
 *
 * Names are `Search`-prefixed because this is the package ROOT namespace — `Hit`, `settle` or
 * `scheduleQuery` are far too generic to claim from `@graphene/ui`.
 */

/** One hit from GET /search — see apps/api/app/routers/search.py. What a session may see is the
 *  API's decision (RLS + permissions), never this layer's. */
export type SearchHit = {
	type: string;
	id: string;
	/** Optional public handle of the page that owns this result. A transaction and a holding do
	 * not have client-portal detail routes of their own, so each targets its account while keeping
	 * its own stable `id` for selection and rendering. */
	target?: string | null;
	title: string;
	subtitle: string | null;
	meta: string | null;
	/** One-based relevance order across entity types when the backend can compare them. */
	rank?: number | null;
};
export type SearchGroup = { type: string; label: string; hits: SearchHit[] };
export type SearchResults = { query: string; groups: SearchGroup[]; truncated: boolean };

/** Where each entity type lives in a portal — the app's policy map. */
export type SearchRoutes = Record<string, (id: string) => string>;

/**
 * The firm-book search catalogue. Platform and admin's operate palette share these categories
 * and route keys; each portal still owns the href a hit opens.
 */
export const BOOK_SEARCH_SCOPES = [
	{ type: null, label: 'All categories', command: 'everywhere' },
	{ type: 'client', label: 'Clients', command: 'clients' },
	{ type: 'account', label: 'Accounts', command: 'accounts' },
	{ type: 'document', label: 'Documents', command: 'documents' },
	{ type: 'case', label: 'Journeys', command: 'journeys' },
	{ type: 'transaction', label: 'Transactions', command: 'transactions' },
	{ type: 'message', label: 'Messages', command: 'messages' },
	{ type: 'review', label: 'Reviews', command: 'reviews' }
] as const;

export type BookSearchScope = (typeof BOOK_SEARCH_SCOPES)[number]['type'];

/** Entity keys both firm-book palettes must route. `message_group` is a route, not its own scope. */
export const BOOK_ROUTE_KEYS = [
	'client',
	'account',
	'document',
	'case',
	'transaction',
	'message',
	'message_group',
	'review'
] as const;

export type BookRouteKey = (typeof BOOK_ROUTE_KEYS)[number];

// Mirrors search.py's MIN_QUERY_LENGTH: a single character matches most of a book and isn't worth
// a round trip. SEARCH_DEBOUNCE_MS is the pause before a keystroke becomes a request.
export const SEARCH_MIN_QUERY_LENGTH = 2;
export const SEARCH_DEBOUNCE_MS = 180;
// A search is an interactive hint, not a report. If the proxy or one of its data sources stops
// answering, leave "Searching…" and offer Retry rather than holding the palette open for ever.
export const SEARCH_TIMEOUT_MS = 5000;

/** Groups with the unroutable hits removed, and any group left empty by that dropped — so no
 *  heading ever sits over nothing. */
export function routableSearchGroups(
	results: SearchResults | null,
	routes: SearchRoutes
): SearchGroup[] {
	return (results?.groups ?? [])
		.map((g) => ({ ...g, hits: g.hits.filter((h) => h.type in routes) }))
		.filter((g) => g.hits.length > 0);
}

/** The path a hit opens, or `null` when this portal has no route for its type. */
export function searchHrefFor(hit: SearchHit, routes: SearchRoutes): string | null {
	return routes[hit.type]?.(hit.target ?? hit.id) ?? null;
}

/** The outcome of one query: fresh results, or a failure the palette shows as a retryable banner.
 *  `null` from `run` means "superseded" — a newer query started, so leave the state untouched. */
export type SearchOutcome = { ok: true; results: SearchResults } | { ok: false };

/**
 * Sequence-guarded search caller. Every request takes a ticket; a response is only ever handed
 * back if its ticket is still the newest, so a slow earlier response can never overwrite a newer
 * one (type "sm", then "smith": "sm"'s late reply must not repaint the panel).
 */
export class SearchClient {
	private seq = 0;
	private active: AbortController | null = null;

	constructor(
		private readonly url: (q: string) => string,
		private readonly fetchImpl: typeof globalThis.fetch = (...args) => globalThis.fetch(...args),
		private readonly timeoutMs: number = SEARCH_TIMEOUT_MS
	) {}

	/** Invalidate anything in flight — the query fell below the minimum length, or the palette
	 *  closed. */
	cancel(): void {
		this.seq++;
		this.active?.abort();
		this.active = null;
	}

	async run(q: string): Promise<SearchOutcome | null> {
		const mine = ++this.seq;
		// Sequence guarding stops an old RESPONSE repainting a new query; aborting stops the old
		// REQUEST continuing to occupy the portal proxy and API while the user keeps typing.
		this.active?.abort();
		const controller = new AbortController();
		this.active = controller;
		const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
		try {
			const res = await this.fetchImpl(this.url(q), { signal: controller.signal });
			if (mine !== this.seq) return null;
			if (!res.ok) return { ok: false };
			const results = (await res.json()) as SearchResults;
			return mine === this.seq ? { ok: true, results } : null;
		} catch {
			return mine === this.seq ? { ok: false } : null;
		} finally {
			clearTimeout(timeout);
			if (this.active === controller) this.active = null;
		}
	}
}

/** A portal's own proxy route (`src/routes/search/+server.ts` in each app, which holds the access
 *  token — and re-checks the permission where one gates the search). */
export const searchUrl = (q: string): string => `/search?q=${encodeURIComponent(q)}`;

/** What one settled query does to the palette. `null` means the query was superseded — a newer
 *  one is in flight, so the palette must leave its state exactly as it is rather than clearing
 *  it. */
export function settleSearch(
	outcome: SearchOutcome | null
): { results: SearchResults | null; failed: boolean } | null {
	if (outcome === null) return null;
	return outcome.ok ? { results: outcome.results, failed: false } : { results: null, failed: true };
}

/** The two things a keystroke can lead to. */
export type SearchQuerySink = {
	/** Run the search — once, after the pause, for a query worth a round trip. */
	query: (q: string) => void;
	/** The query is too short: drop anything in flight and clear the palette. Runs immediately. */
	idle: () => void;
};

/**
 * The debounce, extracted from the components so its rules are testable rather than inferred from
 * an `$effect` body: below `SEARCH_MIN_QUERY_LENGTH` nothing is scheduled and `idle` runs at once;
 * otherwise `query` runs after `ms` unless the returned cleanup runs first — which is exactly what
 * the next keystroke does. Return the result straight from the component's `$effect`.
 */
export function scheduleSearch(
	q: string,
	sink: SearchQuerySink,
	ms: number = SEARCH_DEBOUNCE_MS
): (() => void) | undefined {
	if (q.length < SEARCH_MIN_QUERY_LENGTH) {
		sink.idle();
		return undefined;
	}
	const timer = setTimeout(() => sink.query(q), ms);
	return () => clearTimeout(timer);
}

/** Flatten groups into the backend's one cross-entity relevance order. Payloads without a rank
 *  (the client portal's) keep their stable group/row order. */
export function relevanceOrderedSearchHits(groups: readonly SearchGroup[]): SearchHit[] {
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

/** One category a palette can narrow to. `type` is the API's group type. */
export type SearchCategory = { type: string; label: string };

/**
 * The contextual category strip is made from what THIS query actually returned, not from the
 * whole catalogue — Spotlight's useful idea: a two-kind result offers two choices instead of nine.
 * The order is the caller's stable `scopes` order rather than the API's rank. `alias` folds a
 * group type onto the scope that owns it (the platform's `message_group` → `message`).
 */
export function searchCategoriesForGroups<C extends SearchCategory>(
	groups: readonly Pick<SearchGroup, 'type'>[],
	scopes: readonly C[],
	alias: (type: string) => string = (type) => type
): C[] {
	const returned = new Set(groups.map((group) => alias(group.type)));
	return scopes.filter((scope) => returned.has(scope.type));
}

export type SearchHighlightPart = { text: string; matched: boolean };

/**
 * Split visible result text into safe-to-render plain-text runs. Matching is case- and
 * accent-insensitive, like the database's normalised search, while the slices retain the original
 * spelling (`José` stays `José`). Returning data rather than markup keeps result text out of
 * `{@html}` and therefore out of an injection path.
 *
 * This deliberately marks only literal visible evidence. PostgreSQL can also return a result from
 * a typo-tolerant trigram match or a non-displayed identifier; guessing a highlighted word in that
 * case would claim an explanation the API did not provide.
 */
export function searchHighlightParts(value: string, query: string): SearchHighlightPart[] {
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

	const haystack = folded.join('');
	const needles = [
		...new Set(
			query
				.trim()
				.split(/\s+/)
				.map((token) => token.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase())
				.filter(Boolean)
		)
	].sort((a, b) => b.length - a.length);
	if (!haystack || needles.length === 0) return [{ text: value, matched: false }];

	const ranges: { start: number; end: number }[] = [];
	for (const needle of needles) {
		for (let at = haystack.indexOf(needle); at !== -1; at = haystack.indexOf(needle, at + 1)) {
			const start = starts[at];
			const end = ends[at + needle.length - 1];
			if (start !== undefined && end !== undefined) ranges.push({ start, end });
		}
	}
	if (ranges.length === 0) return [{ text: value, matched: false }];

	ranges.sort((a, b) => a.start - b.start || a.end - b.end);
	const merged: { start: number; end: number }[] = [];
	for (const range of ranges) {
		const previous = merged.at(-1);
		if (previous && range.start <= previous.end) previous.end = Math.max(previous.end, range.end);
		else merged.push({ ...range });
	}

	const parts: SearchHighlightPart[] = [];
	let cursor = 0;
	for (const range of merged) {
		if (range.start > cursor)
			parts.push({ text: value.slice(cursor, range.start), matched: false });
		parts.push({ text: value.slice(range.start, range.end), matched: true });
		cursor = range.end;
	}
	if (cursor < value.length) parts.push({ text: value.slice(cursor), matched: false });
	return parts;
}

/**
 * The bridge between a page and the portal's one search palette. The palette lives in the app
 * shell; the controls that want to open it (the browse bar's search button on the Docs and
 * Insights pages) live several components down inside `@graphene/ui` with no shared ancestor
 * state — so a window event carries the request, with the category to open on. SSR-safe: with
 * no window there is nobody to tell, and the call is a no-op.
 */
export const SEARCH_OPEN_EVENT = 'graphene:search-open';

export type SearchOpenDetail = { scope: string | null };

export function openSearchPalette(scope: string | null = null): void {
	if (typeof window === 'undefined') return;
	window.dispatchEvent(new CustomEvent<SearchOpenDetail>(SEARCH_OPEN_EVENT, { detail: { scope } }));
}

/**
 * The Spotlight panel's geometry, shared by every portal's palette so the two cannot drift
 * (`docs/search.md`, "The panel").
 *
 * From `sm` up it is the floating glass bar: centred on the WINDOW (`left-1/2` cancels the shared
 * dialog's canvas inset), its top derived from the one knob `--spotlight-max-h` so it grows
 * downward from a fixed line and the unfolded panel sits centred.
 *
 * Below `sm` it is a full-screen search view, modelled on Monzo's (and the App Store's, and
 * Material's SearchView): a floating box with a keyboard under it leaves a slot of results
 * between two chromes, so the search takes the whole screen — a filled pill field pinned at the
 * top with a round close button beside it (SPOTLIGHT_PHONE_CLOSE_CLASS), the category pills under
 * it with no rule between, the results filling the rest. The knob becomes `100dvh`, the geometry
 * follows from it, the corners and glass go (a sheet over the whole page has nothing to refract,
 * so it sits on the solid background), and the dialog's own close button hides in favour of the
 * round one the palette draws.
 */
export const SPOTLIGHT_PANEL_CLASS = [
	// material + motion
	'glass spotlight-glass motion-menu flex flex-col overflow-hidden rounded-xl! bg-(--glass-bg) p-0 ring-0 shadow-(--glass-shadow)',
	// the floating bar (sm+); the knob is a class, not inline, so the mobile arm below can override it
	'[--spotlight-max-h:70dvh] top-[calc((100dvh_-_var(--spotlight-max-h))_/_2)] left-1/2 max-h-(--spotlight-max-h) md:max-w-2xl',
	// the full-screen view (below sm)
	'max-sm:[--spotlight-max-h:100dvh] max-sm:inset-0 max-sm:h-dvh max-sm:w-full max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none! max-sm:bg-background max-sm:shadow-none max-sm:[&_[data-slot=dialog-close]]:hidden',
	// the field's chrome inside the command primitive
	'[&>[data-slot=command]]:min-h-0 [&_[data-slot=command-input-wrapper]]:p-3 [&_[data-slot=command-input-wrapper]]:pr-14 [&_[data-slot=input-group]]:h-14! [&_[data-slot=input-group]]:rounded-xl! [&_[data-slot=input-group]]:bg-transparent! [&_[data-slot=input-group]]:border-transparent! [&_[data-slot=input-group-addon]]:*:size-5! [&_[data-slot=dialog-close]]:top-7! [&_[data-slot=dialog-close]]:right-5!',
	// the phone's field: a filled pill, room on its right for the round close
	'max-sm:[&_[data-slot=command-input-wrapper]]:pr-[4.5rem] max-sm:[&_[data-slot=input-group]]:h-12! max-sm:[&_[data-slot=input-group]]:rounded-full! max-sm:[&_[data-slot=input-group]]:bg-muted! max-sm:[&_[data-slot=command-input]]:text-[1.0625rem] max-sm:[&_[data-slot=command-input]]:font-normal'
].join(' ');

/** The phone's way out, beside the pill field: a round close the size of the field's height. The
 *  dialog's own X hides below `sm` (SPOTLIGHT_PANEL_CLASS), so this is the only one there. */
export const SPOTLIGHT_PHONE_CLOSE_CLASS =
	'bg-muted text-foreground hover:bg-muted/80 absolute top-4 right-4 z-10 flex size-12 items-center justify-center rounded-full transition-colors sm:hidden [&_svg]:size-5';

/** A category pill. Compact text chips in the floating bar; on a phone, Monzo's outlined pills at
 *  thumb size. The pressed state is the caller's. */
export const SPOTLIGHT_CHIP_CLASS =
	'h-7 shrink-0 rounded-full px-2.5 text-xs max-sm:h-9 max-sm:border max-sm:border-border/70 max-sm:px-4 max-sm:text-sm';

/** The scrim goes entirely, tint and blur: a dimmed, blurred page leaves glass nothing to refract. */
export const SPOTLIGHT_OVERLAY_CLASS =
	'bg-transparent dark:bg-transparent supports-backdrop-filter:backdrop-blur-none';

/** The scoped glass knobs — inline on purpose: theme.css re-derives the glass fills only from an
 *  inline `--glass-*` style (`[style*='--glass']`). The geometry knob is in the class. */
export const SPOTLIGHT_PANEL_STYLE =
	'--glass-blur: 20px; --glass-tint: 84%; --glass-sheen-strength: 1';

/** Does this keydown mean "open/close the palette"? ⌘K on macOS, Ctrl+K elsewhere. */
export function isSearchShortcut(e: {
	key: string;
	metaKey?: boolean;
	ctrlKey?: boolean;
}): boolean {
	return Boolean(e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
}
