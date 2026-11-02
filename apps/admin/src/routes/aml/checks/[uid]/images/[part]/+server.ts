import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

/** The sides of an identity check the API can stream a photo for. */
const PARTS = ['front', 'back', 'selfie'] as const;

// Stream one photo of an identity check through a SAME-ORIGIN proxy, mirroring
// `documents/[uid]/content`: the browser never talks to the API or ComplyCube directly —
// browser -> this route -> API `/aml/checks/{uid}/images/{part}` -> ComplyCube.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:read')) error(403, 'Not authorised');
	if (!(PARTS as readonly string[]).includes(params.part)) error(404, 'No such image.');

	const res = await fetch(
		`${apiUrl()}/aml/checks/${encodeURIComponent(params.uid)}/images/${params.part}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) {
		if (res.status === 404 || res.status === 409) error(res.status, await detail(res));
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
