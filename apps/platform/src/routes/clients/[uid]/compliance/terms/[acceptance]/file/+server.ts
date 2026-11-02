import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

// The file of the terms version a client accepted, through a same-origin relay:
// browser -> this route -> API `/contacts/{uid}/term-acceptances/{acceptance}/file`.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'contacts:read')) error(403, 'Not authorised');
	let res: Response;
	try {
		res = await fetch(
			`${apiUrl()}/contacts/${encodeURIComponent(params.uid)}/term-acceptances/${encodeURIComponent(params.acceptance)}/file`,
			{ headers: authHeaders(locals.accessToken) }
		);
	} catch {
		error(503, 'The terms are unavailable.');
	}
	if (!res.ok) {
		if (res.status === 403 || res.status === 404) error(res.status, await detail(res));
		error(502, 'The terms are unavailable.');
	}
	// Buffered, as the documents relay does: a chunked response with no Content-Length can render
	// blank in an embedded viewer.
	return new Response(await res.arrayBuffer(), {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/pdf',
			'content-disposition': res.headers.get('content-disposition') ?? 'inline',
			'cache-control': 'private, no-store'
		}
	});
};
