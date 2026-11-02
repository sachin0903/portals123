import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Proxy a staff-side attachment send — rebuild the multipart body server-side (never trust the
 * browser's own boundary/headers through to the API) and forward the caller's own session token,
 * same shape as the JSON `[id]/+server.ts` POST this sits beside. */
export const POST: RequestHandler = async ({ locals, fetch, params, request }) => {
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
	const res = await fetch(`${apiUrl()}/messaging/conversations/${params.uid}/attachments`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken),
		body
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
