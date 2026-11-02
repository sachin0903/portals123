import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Lazy PDF preview of the template's latest saved version — the true-to-generation render (the same
// engine generation uses, via the worker's render service), streamed for the overview page's
// <iframe>. GET so the browser loads it after the page paints (the page render never blocks on it).
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'templates:read')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/templates/${params.uid}/preview.pdf`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Could not render the preview');
	return new Response(await res.arrayBuffer(), {
		headers: { 'content-type': 'application/pdf' }
	});
};
