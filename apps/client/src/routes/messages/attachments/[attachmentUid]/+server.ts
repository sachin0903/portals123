import { RelayTooLarge, relayBuffered } from '@graphene/auth';
import { apiUrl, authHeaders, publicId } from '$lib/api';
import type { RequestHandler } from './$types';

// Stream a chat attachment's bytes through a SAME-ORIGIN proxy, mirroring the platform portal's
// equivalent route — the object store is compose-internal, so a presigned URL wouldn't resolve
// in the browser. Fetched only when the download link is clicked, never on chat-history load.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) return new Response(null, { status: 401 });
	const uid = publicId(params.attachmentUid);
	if (!uid) return new Response(null, { status: 404 });
	const res = await fetch(`${apiUrl()}/messaging/thread/attachments/${uid}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return new Response(null, { status: res.status === 404 ? 404 : 502 });
	try {
		return await relayBuffered(res, { fallbackDisposition: 'attachment' });
	} catch (e) {
		if (e instanceof RelayTooLarge) return new Response(null, { status: 502 });
		throw e;
	}
};
