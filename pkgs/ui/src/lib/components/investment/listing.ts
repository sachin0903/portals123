/**
 * Listing toolbar logic — the pure half of the widgets' search/filter affordances (the legacy
 * /holdings, /activity and /documents pages' toolbars, absorbed into the blocks). Kept out of
 * the components so the matching, option-derivation and unread-count rules are table-testable
 * (listing.test.ts) and shared verbatim between every look of a listing widget.
 */
import type { SelectMenuOption } from '../ui/select-menu/select-menu.svelte';
import type { ClientDocument } from './types.js';

/** The externally-controlled filter state PortalPage can thread from the app (next wave: the
 * app owns it via URL params and re-fetches server-side). Absent keys mean "unfiltered". */
export interface ListingQuery {
	q?: string;
	account?: string;
}

/** The account SelectMenu's "no filter" sentinel — mirrors the legacy pages' 'all'. */
export const ALL = 'all';

/** Case-insensitive substring match over a row's searchable texts (the legacy pages' rule:
 * `String.includes` on the lowercased trim). A blank query matches everything. */
export function matchesText(texts: (string | null | undefined)[], query: string): boolean {
	const needle = query.trim().toLowerCase();
	if (!needle) return true;
	return texts.some((t) => (t ?? '').toLowerCase().includes(needle));
}

/**
 * The account filter's options: 'All accounts' plus each distinct non-blank account label in
 * appearance order (the legacy pages derived theirs from the rows the same way). The caller
 * hides the control when there is under 2 real options — a one-account filter can't filter
 * anything.
 *
 * `labels` is the client's WHOLE account list where the caller knows it, and only the rows in
 * hand where it does not. The distinction is the whole of why this takes an argument rather
 * than reading the rows itself: under a SERVER-side filter the rows in hand are one account's,
 * so a list derived from them collapses to that one account, the control hides itself for
 * having nothing to offer, and the filter can never be cleared — the dead end this parameter
 * exists to prevent.
 *
 * `active` is the filter currently applied. It is appended when the label list does not carry
 * it — a renamed or closed account, or a pasted link naming one that no longer resolves — so
 * that a filter which IS in force is always one the reader can see and switch off. An option
 * nobody can select is how a filtered listing comes to read as an empty book.
 */
export function accountItems(
	labels: (string | null | undefined)[],
	active: string = ALL
): SelectMenuOption[] {
	const seen = new Set<string>();
	const options: SelectMenuOption[] = [{ value: ALL, label: 'All accounts' }];
	for (const label of labels) {
		const name = label?.trim();
		if (!name || seen.has(name)) continue;
		seen.add(name);
		options.push({ value: name, label: name });
	}
	const current = active.trim();
	if (current && current !== ALL && !seen.has(current))
		options.push({ value: current, label: current });
	return options;
}

/** One category filter pill: the category label doubles as the value (ClientDocument categories
 * are already display vocabulary), with its row count for the "(n)" suffix. */
export interface CategoryPill {
	value: string;
	count: number;
}

/** The category pills, enumerated from whatever categories are actually in the data (no fixed
 * list to fall out of step with — the legacy /documents page's rule), in appearance order. */
export function categoryCounts(categories: string[]): CategoryPill[] {
	const counts = new Map<string, number>();
	for (const c of categories) counts.set(c, (counts.get(c) ?? 0) + 1);
	return [...counts].map(([value, count]) => ({ value, count }));
}

/**
 * The unread-count note ("3 unread"), or null when it must not render: the payload carries no
 * read-state at all (no document has the `unread` field — a loader that doesn't know must not
 * imply "everything's read"), or nothing is unread (the legacy page showed its badge only for
 * a positive count).
 */
export function unreadNote(documents: ClientDocument[]): string | null {
	if (!documents.some((d) => d.unread !== undefined)) return null;
	const n = documents.filter((d) => d.unread === true).length;
	return n > 0 ? `${n} unread` : null;
}

/** Per-row accessors a listing filter needs; each axis optional — a listing without accounts
 * (documents) or categories (holdings/activity) simply never filters on it. */
export interface ListingAccessors<T> {
	/** The texts the search box matches over (name + ticker, description + type, …). */
	searchText: (row: T) => (string | null | undefined)[];
	accountOf?: ((row: T) => string | null | undefined) | null;
	categoryOf?: ((row: T) => string) | null;
}

/** One pass of the toolbar's client-side filter: account, category, then text — all axes AND
 * together, exactly like the legacy pages. `ALL` (or a missing accessor) skips an axis. */
export function filterListing<T>(
	rows: T[],
	state: { q: string; account: string; category: string },
	accessors: ListingAccessors<T>
): T[] {
	return rows.filter((row) => {
		if (
			state.account !== ALL &&
			accessors.accountOf &&
			(accessors.accountOf(row) ?? '').trim() !== state.account
		)
			return false;
		if (
			state.category !== ALL &&
			accessors.categoryOf &&
			accessors.categoryOf(row) !== state.category
		)
			return false;
		return matchesText(accessors.searchText(row), state.q);
	});
}
