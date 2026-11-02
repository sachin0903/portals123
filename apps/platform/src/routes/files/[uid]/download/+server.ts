import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Stream a file's bytes through a SAME-ORIGIN proxy (mirrors the documents content route): the
// object store is compose-internal, so a presigned URL wouldn't resolve in the browser. The API
// sets Content-Type + Content-Disposition (inline for PDFs/images, attachment otherwise) with an
// RFC-6266 filename; we buffer and pass them through.
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'documents:read')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/files/${params.uid}/content`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'File unavailable');
	const body = await res.arrayBuffer();
	// `?preview=1` is a grid thumbnail: a short private cache so a view toggle reuses it.
	const preview = url.searchParams.get('preview') === '1';
	return new Response(body, {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/octet-stream',
			'content-disposition': res.headers.get('content-disposition') ?? 'inline',
			'cache-control': preview ? 'private, max-age=300' : 'private, no-store'
		}
	});
};
