import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { NotificationItemData } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { PageServerLoad } from './$types';

// Only these values are meaningful to the API — anything else (a stale bookmark, a hand-edited
// URL) is treated as "no filter" rather than forwarded, so `?state=bogus` behaves exactly like
// the filter was never touched instead of the request failing further down.
const STATES = ['unread', 'read', 'dismissed'];
const PRIORITIES = ['low', 'normal', 'high'];

// A bound the endpoint can cast: `since`/`until` reach `CAST(:since AS date)`, and junk 5xxs it.
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function multi(params: URLSearchParams, key: string, allowed?: readonly string[]): string[] {
	const values = params.getAll(key).filter(Boolean);
	return [...new Set(allowed ? values.filter((value) => allowed.includes(value)) : values)];
}

function bound(params: URLSearchParams, key: string): string {
	const raw = params.get(key) ?? '';
	return ISO_DATE.test(raw) ? raw : '';
}

/** The client's own notification feed (RLS-scoped to their contact by the API), narrowed to
 * the actively-selected linked contact for a login linked to more than one.
 *
 * Also the archive's search/filter surface: `q`, `category`, `priority` and `state` all come
 * straight off the URL, so the toolbar's state IS the address bar and a change to any of them is
 * a fresh navigation rather than local component state (see the page's `onquery`). */
export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	const pageNumber = Math.max(1, Number(url.searchParams.get('page') ?? 1) || 1);
	const q = url.searchParams.get('q') ?? '';
	// No allowlist for `category`: its vocabulary is the firm's own rows, not a fixed list.
	const category = multi(url.searchParams, 'category');
	const priority = multi(url.searchParams, 'priority', PRIORITIES);
	const state = multi(url.searchParams, 'state', STATES);
	const since = bound(url.searchParams, 'since');
	const until = bound(url.searchParams, 'until');
	// A filter that is OFF is absent, never an empty list — the funnel badges the keys it is given.
	const filters: Record<string, string | string[]> = {
		...(category.length ? { category } : {}),
		...(priority.length ? { priority } : {}),
		...(state.length ? { state } : {}),
		...(since ? { since } : {}),
		...(until ? { until } : {})
	};
	const empty = {
		notifications: [] as NotificationItemData[],
		hasMore: false,
		page: pageNumber,
		query: q,
		filters,
		facets: { category: [], priority: [] } as Record<string, string[]>,
		loadError: false
	};
	if (!locals.accessToken) return empty;
	const { activeContactId, onboarding } = await parent();
	if (onboarding) redirect(303, '/onboarding');
	const offset = (pageNumber - 1) * PAGE_SIZE;
	const params = new URLSearchParams({
		limit: String(PAGE_SIZE),
		offset: String(offset),
		include_dismissed: 'true'
	});
	if (activeContactId !== null) params.set('contact_id', String(activeContactId));
	// `q` is free text someone typed, so it goes through URLSearchParams rather than the old
	// template-literal concatenation — a raw `&` or `#` in a search term must not corrupt the
	// query string it's appended to.
	if (q) params.set('q', q);
	for (const value of category) params.append('category', value);
	for (const value of priority) params.append('priority', value);
	for (const value of state) params.append('state', value);
	// `until` is compared against the END of its day server-side.
	if (since) params.set('since', since);
	if (until) params.set('until', until);
	// This route is the full archive, not the bell's short unread-first list, so it asks for
	// dismissed rows too — a row leaving the bell must never make it look like it never happened.
	// A refused or unreachable API is a `loadError`, never an empty archive: an inbox that reads
	// "nothing here" while the API is 500ing hides exactly the outage notices it exists to keep.
	const res = await fetchJson<{
		items: NotificationItemData[];
		has_more: boolean;
		facets?: Record<string, string[]>;
	}>(fetch, `${apiUrl()}/notifications?${params.toString()}`, authHeaders(locals.accessToken));
	if (!res.ok) return { ...empty, loadError: true };
	const page = res.data;
	return {
		notifications: page.items,
		hasMore: page.has_more,
		page: pageNumber,
		query: q,
		filters,
		facets: page.facets ?? { category: [], priority: [] },
		loadError: false
	};
};
