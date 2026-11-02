import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** List a template's version history (proxied so the access token stays server-side). */
/** List the versions — or ONE of them with `?vid=`, content included, so a version can be LOOKED AT
 *  and not only restored. See the admin sibling's note for why it is the same route. */
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'email_templates:read')) error(403, 'Not permitted');
	const vid = url.searchParams.get('vid');
	const target = `${apiUrl()}/email-templates/${params.uid}/versions${
		vid ? `/${encodeURIComponent(vid)}` : ''
	}`;
	const res = await fetch(target, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};

/** Restore a version → the API copies it into a NEW draft (never overwrites / auto-publishes). */
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'email_templates:write')) error(403, 'Not permitted');
	const body = await request.json().catch(() => ({}));
	const vid = String(body.vid ?? '');
	if (!vid) error(400, 'No version specified');
	const res = await fetch(`${apiUrl()}/email-templates/${params.uid}/versions/${vid}/restore`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
