import { RelayTooLarge, relayBuffered } from '@graphene/auth';
import { error } from '@sveltejs/kit';
import { apiUrl, authHeaders, publicId } from '$lib/api';
import type { RequestHandler } from './$types';

// Stream a supporting file's bytes through a SAME-ORIGIN proxy (mirrors the documents content
// route): the object store is compose-internal, so a presigned URL wouldn't resolve in the
// browser. The API sets Content-Type + Content-Disposition (inline for PDFs/images, attachment
// otherwise) with an RFC-6266 filename; we buffer and pass them through so previews render inline
// and the download button saves under the file's real name.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	const uid = publicId(params.uid);
	if (!uid) error(404, 'File unavailable');
	const res = await fetch(`${apiUrl()}/files/${uid}/content`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'File unavailable');
	try {
		return await relayBuffered(res);
	} catch (e) {
		if (e instanceof RelayTooLarge) error(502, 'File unavailable');
		throw e;
	}
};
