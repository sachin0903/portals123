import { RelayTooLarge, relayBuffered } from '@graphene/auth';
import { error } from '@sveltejs/kit';
import { apiUrl, authHeaders, publicId } from '$lib/api';
import type { RequestHandler } from './$types';

// Stream a private document's bytes to the browser through a SAME-ORIGIN proxy. The object store
// is compose-internal (no public endpoint), so a presigned URL wouldn't resolve in the browser —
// instead we relay: browser -> this route -> API `/content` (internal) -> object storage. The API
// sets Content-Type + Content-Disposition (inline for PDFs/images); we pass them through so the
// viewer previews inline and the download button (with its `download` attr) still forces a save.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const uid = publicId(params.uid);
	if (!uid) error(404, 'Document unavailable');
	const res = await fetch(`${apiUrl()}/documents/${uid}/content`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Document unavailable');
	// Buffer rather than passing the stream through: a chunked iframe response with no
	// Content-Length makes Chromium's embedded PDF viewer render blank intermittently (the
	// "200 but blank preview" bug). Documents are small, so buffering is cheap — and BOUNDED:
	// `relayBuffered` stops at the API's own object ceiling rather than holding whatever arrives.
	try {
		return await relayBuffered(res);
	} catch (e) {
		if (e instanceof RelayTooLarge) error(502, 'Document unavailable');
		throw e;
	}
};
