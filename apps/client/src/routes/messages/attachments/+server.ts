import { json } from '@sveltejs/kit';
import { contactIdQuery } from '$lib/active-contact';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Attachment send for the client's own thread — mirrors the platform portal's
 * `/chats/[id]/attachments/+server.ts`. Rebuilds the multipart body server-side (never trust the
 * browser's own boundary/headers through to the API) and forwards the caller's own session
 * token. Sits beside `[attachmentId]/+server.ts` (the GET download proxy) at the same route.
 * `request_item_uid`, when the incoming form carries one (the "tap to upload" fulfilment card,
 * never the plain paperclip), MUST be forwarded too — dropping it silently downgrades a
 * fulfilment into an unlinked attachment: the item never gets marked fulfilled, and (for a
 * group-raised request) the response never gets tagged into that group's own feed either.
 * `contact_id`, when the form carries one, selects which linked contact's thread — forwarded
 * as a QUERY param on the API call (the API route mixes it with File/Form params, so a plain
 * scalar there resolves from the query string, not the multipart body). */
export const POST: RequestHandler = async ({ locals, fetch, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const incoming = await request.formData();
	// EVERY `file` part, not the first. A message may carry several attachments (0123) and the
	// composer appends one part per staged file, but `get` returns only the first — so a
	// three-file send silently uploaded one and reported success. The API takes a repeated part
	// (`list[UploadFile]`); this rebuilds it verbatim rather than collapsing it.
	const files = incoming.getAll('file').filter((f): f is File => f instanceof File && f.size > 0);
	if (files.length === 0) {
		return json({ error: 'Choose a file to attach.' }, { status: 422 });
	}
	const body = new FormData();
	for (const file of files) body.append('file', file, file.name);
	body.append('text', String(incoming.get('text') ?? ''));
	const requestItemUid = incoming.get('request_item_uid');
	if (requestItemUid) body.append('request_item_uid', String(requestItemUid));
	const cid = contactIdQuery(incoming.get('contact_id'));
	const res = await fetch(`${apiUrl()}/messaging/thread/attachments${cid}`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken),
		body
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
