import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Stream a chat attachment's bytes through a SAME-ORIGIN proxy, mirroring
// documents/[id]/content/+server.ts — the object store is compose-internal, so a presigned URL
// wouldn't resolve in the browser. Fetched only when the download button is clicked, never on
// chat-history load.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) return new Response(null, { status: 401 });
	const res = await fetch(
		`${apiUrl()}/messaging/conversations/${params.uid}/attachments/${params.attachmentUid}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) return new Response(null, { status: res.status === 404 ? 404 : 502 });
	const body = await res.arrayBuffer();
	return new Response(body, {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/octet-stream',
			'content-disposition': res.headers.get('content-disposition') ?? 'attachment',
			'cache-control': 'private, no-store'
		}
	});
};
