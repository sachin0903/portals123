import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Stop waiting for a document that has not rendered, and stop it being rendered. A proxy rather
// than a form action because the generating screen is mid-flow: it has a poll running and needs an
// answer it can show, not a navigation.
export const POST: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'documents:write')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/documents/${params.uid}/cancel-render`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken)
	});
	const body = await res.json().catch(() => ({}));
	if (!res.ok) {
		error(res.status, (body as { detail?: string }).detail ?? 'Could not cancel the render.');
	}
	return json(body);
};
