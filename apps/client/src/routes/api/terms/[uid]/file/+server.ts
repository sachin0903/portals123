import { RelayTooLarge, relayBuffered } from '@graphene/auth';
import { error } from '@sveltejs/kit';
import { apiUrl, authHeaders, publicId } from '$lib/api';
import type { RequestHandler } from './$types';

// A catalogue term's published PDF, through a SAME-ORIGIN proxy: browser -> this route -> API
// `/terms/{uid}/file` -> object storage. The onboarding page lists the firm's outstanding terms
// that no step of the journey pins, and a term authored as a file has no request item and so no
// `/api/request-items/.../content` to read it through. Same two reasons as that route: the object
// store has no public endpoint in a container deployment, and pdf.js fetches the bytes rather than
// navigating to them, so a cross-origin URL needs CORS the store does not send.
//
// The API's route is the authorisation boundary: it serves the published snapshot only, from the
// caller's own firm's store.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const uid = publicId(params.uid);
	if (!uid) error(404, 'Document unavailable');
	const res = await fetch(`${apiUrl()}/terms/${uid}/file`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Document unavailable');
	// Buffered, not streamed: a chunked response with no Content-Length makes an embedded PDF
	// render blank intermittently. Terms are small, so buffering is cheap — and bounded.
	try {
		return await relayBuffered(res);
	} catch (e) {
		if (e instanceof RelayTooLarge) error(502, 'Document unavailable');
		throw e;
	}
};
