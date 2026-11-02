import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, type IoClientSearchResult } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/**
 * The IO Clients dropdown: Intelliflo's own candidates for the just-picked platform client,
 * searched server-side by NI number (when the client has one on file) or first/last name.
 */
export const GET: RequestHandler = async ({ locals, url, fetch }) => {
	if (!locals.user) error(401, 'Not authenticated');
	if (!can(locals.permissions, locals.role, 'integrations:read')) error(403, 'Not authorised');

	const contactUid = url.searchParams.get('contact_uid');
	if (!contactUid) error(400, 'contact_uid is required');

	const res = await fetch(`${apiUrl()}/integrations/clients/${contactUid}/io-clients`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json((await res.json()) as IoClientSearchResult[]);
};
