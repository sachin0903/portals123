import { json, error } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { canSearch } from '$lib/search';
import type { RequestHandler } from './$types';

// Server-side proxy for the operations header search bar. Firms only — the ops surface has no
// cross-tenant client/document search, and the API re-checks `tenants:read` regardless.
//
// `canSearch` is the same predicate `$lib/SearchBar.svelte` hides itself on, so the bar and its
// proxy cannot disagree about who may search.
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!canSearch(locals.permissions, locals.role)) error(403, 'Not permitted');
	const q = url.searchParams.get('q') ?? '';
	const res = await fetch(`${apiUrl()}/search/tenants?q=${encodeURIComponent(q)}&limit=8`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Search is unavailable just now.');
	return json(await res.json());
};
