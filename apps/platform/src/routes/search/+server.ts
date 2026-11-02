import { json, error } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Server-side proxy for the header search bar (a client component can't hold the access token).
// Forwards to the API's cross-entity /search, which scopes every group by the caller's own RLS
// claims and omits any group whose entities they couldn't open — so there is nothing to re-gate
// here beyond "is there a session at all".
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const q = url.searchParams.get('q') ?? '';
	const scope = url.searchParams.get('scope');
	const allowedScopes = new Set([
		'clients',
		'accounts',
		'documents',
		'journeys',
		'transactions',
		'messages',
		'reviews'
	]);
	const scoped = scope && allowedScopes.has(scope) ? `&scope=${encodeURIComponent(scope)}` : '';
	const res = await fetch(`${apiUrl()}/search?q=${encodeURIComponent(q)}&limit=5${scoped}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Search is unavailable. Try again.');
	return json(await res.json());
};
