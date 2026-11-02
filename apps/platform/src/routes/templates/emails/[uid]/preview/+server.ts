import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';
import { readAudience } from '@graphene/ui/email-editor';

/**
 * Render a sample preview of the editor's CURRENT content — merge fields filled with representative
 * values, CSS folded inline — and return the HTML. Proxies to the API so the access token stays
 * server-side; the browser posts the live canvas subject/preheader/html/css. This NEVER sends: the
 * API's /preview endpoint only merges + inlines.
 */
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'email_templates:read')) error(403, 'Not permitted');

	const body = await request.json().catch(() => ({}));
	const res = await fetch(`${apiUrl()}/email-templates/${params.uid}/preview`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({
			subject: String(body.subject ?? ''),
			preheader: String(body.preheader ?? ''),
			html: String(body.html ?? ''),
			css: String(body.css ?? ''),
			// Which of the firm's two brands dresses it — see the send-test proxy's header.
			audience: readAudience(body.audience)
		})
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
