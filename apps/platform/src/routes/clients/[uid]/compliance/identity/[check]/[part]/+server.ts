import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** The sides of an identity check the API can stream a photo for. */
const PARTS = ['front', 'back', 'selfie'] as const;

// Stream one photo of a client's identity check through a SAME-ORIGIN relay, like
// `documents/[uid]/content`: the browser never talks to the API or the identity provider directly.
// browser -> this route -> API `/crm/contacts/{uid}/identity/checks/{check}/images/{part}`.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'screening:read')) error(403, 'Not authorised');
	if (!(PARTS as readonly string[]).includes(params.part)) error(404, 'No such image.');

	let res: Response;
	try {
		res = await fetch(
			`${apiUrl()}/crm/contacts/${encodeURIComponent(params.uid)}/identity/checks/${encodeURIComponent(params.check)}/images/${params.part}`,
			{ headers: authHeaders(locals.accessToken) }
		);
	} catch {
		error(503, 'The image is unavailable.');
	}
	if (!res.ok) {
		if (res.status === 403 || res.status === 404) error(res.status, await detail(res));
		error(502, 'The image is unavailable.');
	}
	// Buffer the bytes rather than pass the stream through, as the documents relay does: a chunked
	// response with no Content-Length can render blank in an embedded viewer. `new
	// Response(ArrayBuffer)` sets Content-Length itself.
	const body = await res.arrayBuffer();
	const headers: Record<string, string> = {
		'content-type': res.headers.get('content-type') ?? 'application/octet-stream',
		'content-disposition': res.headers.get('content-disposition') ?? 'inline',
		'cache-control': res.headers.get('cache-control') ?? 'private, no-store'
	};
	const xcto = res.headers.get('x-content-type-options');
	if (xcto) headers['x-content-type-options'] = xcto;
	return new Response(body, { headers });
};
