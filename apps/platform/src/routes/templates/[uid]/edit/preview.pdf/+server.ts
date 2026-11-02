import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// True-to-generation preview: render the CURRENT editor schema with the SAME engine generation uses
// (WeasyPrint) and stream back the PDF, so the editor's Preview shows exact pages, overflow and page
// breaks — which the fast HTML preview (a browser layout, never paginated) cannot. Synchronous
// (~seconds); the editor gates it behind an explicit Preview action and falls back to the HTML
// preview if this fails (e.g. the render engine's native libs aren't present in local dev).
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'templates:read')) error(403, 'Not permitted');
	const body = (await request.json().catch(() => ({}))) as { schema?: unknown };
	const res = await fetch(`${apiUrl()}/templates/${params.uid}/preview.pdf`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ schema: body?.schema ?? {} })
	});
	if (!res.ok) error(res.status, 'Could not render the PDF preview');
	// Stream the bytes straight through with the PDF content type so an <iframe> renders it inline.
	return new Response(await res.arrayBuffer(), {
		headers: { 'content-type': 'application/pdf' }
	});
};
