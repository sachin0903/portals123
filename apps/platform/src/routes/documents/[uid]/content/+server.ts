import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Stream a private document's bytes to the browser through a SAME-ORIGIN proxy. The object store
// is compose-internal (no public endpoint), so a presigned URL wouldn't resolve in the browser —
// instead we relay: browser -> this route -> API `/content` (internal) -> object storage. The API
// sets Content-Type + Content-Disposition (inline for PDFs/images); we pass them through so the
// viewer previews inline and the download button (with its `download` attr) still forces a save.
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'documents:read')) error(403, 'Not permitted');
	// `?preview=1` is a grid thumbnail: the API records no view and allows a short private cache.
	const query = url.searchParams.get('preview') === '1' ? '?preview=true' : '';
	const res = await fetch(`${apiUrl()}/documents/${params.uid}/content${query}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Document unavailable');
	const cacheControl = res.headers.get('cache-control') ?? 'private, no-store';
	// 204: a preview of a format a browser cannot draw. No bytes.
	if (res.status === 204) {
		return new Response(null, { status: 204, headers: { 'cache-control': cacheControl } });
	}
	// Buffer the bytes and return a complete, length-known response rather than passing the stream
	// through: a chunked iframe response with no Content-Length makes Chromium's embedded PDF viewer
	// render blank intermittently (the "200 but blank preview" bug). Documents are small, so buffering
	// is cheap. `new Response(ArrayBuffer)` sets Content-Length itself.
	const body = await res.arrayBuffer();
	const headers: Record<string, string> = {
		'content-type': res.headers.get('content-type') ?? 'application/octet-stream',
		'content-disposition': res.headers.get('content-disposition') ?? 'inline',
		'cache-control': cacheControl
	};
	// The API sandboxes served HTML (a terms declaration); keep that defence on this origin.
	for (const name of ['content-security-policy', 'x-content-type-options']) {
		const value = res.headers.get(name);
		if (value) headers[name] = value;
	}
	return new Response(body, { headers });
};
