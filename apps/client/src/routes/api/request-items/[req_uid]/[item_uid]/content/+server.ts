import { RelayTooLarge, relayBuffered } from '@graphene/auth';
import { error } from '@sveltejs/kit';
import { apiUrl, authHeaders, publicId } from '$lib/api';
import type { RequestHandler } from './$types';

// Stream the document an agree/acknowledge item asks the client to review, through a SAME-ORIGIN
// proxy: browser -> this route -> API `/target/content` (internal) -> object storage. Mirrors
// `/documents/[uid]/content`, and for the same two reasons — the object store has no public
// endpoint in a container deployment, and pdf.js fetches the bytes rather than navigating to them,
// so a cross-origin URL needs CORS the store does not send.
//
// The API's own route is the authorisation boundary (the request's contact must be in the caller's
// scope): the client holds no document link to a firm's terms PDF, so `/documents/{uid}/content`
// would refuse it.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const reqUid = publicId(params.req_uid);
	const itemUid = publicId(params.item_uid);
	if (!reqUid || !itemUid) error(404, 'Document unavailable');
	const res = await fetch(
		`${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}/target/content`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Document unavailable');
	// Buffer rather than passing the stream through: a chunked response with no Content-Length
	// makes an embedded PDF render blank intermittently (the "200 but blank preview" bug the
	// documents content route records). Declarations are small, so buffering is cheap — and bounded.
	try {
		return await relayBuffered(res);
	} catch (e) {
		if (e instanceof RelayTooLarge) error(502, 'Document unavailable');
		throw e;
	}
};
