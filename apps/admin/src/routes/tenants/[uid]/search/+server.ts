import { json, error } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { canSearchFirmBook } from '$lib/search';
import { targetTenantUrl } from '$lib/target-tenant';
import type { RequestHandler } from './$types';

// Server-side proxy for the in-firm book search palette. Pins `?tenant=` to the route firm so the
// API's target_tenant_session scopes every group under that firm's RLS claims.
//
// `canSearchFirmBook` is the same predicate the bar hides itself on (`tenant_portal:read`).
export const GET: RequestHandler = async ({ url, params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!canSearchFirmBook(locals.permissions, locals.role)) error(403, 'Not permitted');
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
	const paramsOut: Record<string, string | number> = { q, limit: 5 };
	if (scope && allowedScopes.has(scope)) paramsOut.scope = scope;
	const res = await fetch(targetTenantUrl('/search', params.uid, paramsOut), {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Search is unavailable just now.');
	return json(await res.json());
};
