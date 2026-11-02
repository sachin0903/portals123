import { json, type RequestHandler } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders, detail } from '$lib/api';

/**
 * Write a note on the client a conversation is with, from the conversation.
 *
 * The rail states the recent notes beside the thread; this is the other half, so "they told me on
 * the phone they are retiring in March" can be written down where it was heard rather than after a
 * trip to the record and back — which is the trip on which it gets forgotten.
 *
 * The SAME endpoint the record's own composer posts to (`POST /firm/contacts/{uid}/notes`), gated
 * on the same `contacts:write`, so a note written here is not a second kind of note. This route
 * exists only because the inbox is client-SIDE about which client is selected and cannot use the
 * record's form action.
 *
 * Returns the created note in the shape the rail renders, so the caller can show it without a
 * second read.
 */
export const POST: RequestHandler = async ({ locals, fetch, params, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'contacts:write')) {
		return json({ error: 'You cannot write notes on this client.' }, { status: 403 });
	}

	const body = String(((await request.json()) as { body?: unknown }).body ?? '').trim();
	if (!body) return json({ error: 'Write a note first.' }, { status: 422 });

	const res = await fetch(`${apiUrl()}/firm/contacts/${params.contactUid}/notes`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ body })
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });

	const note = (await res.json()) as {
		id: number;
		uid?: string;
		body: string;
		author: string | null;
		created_at: string;
	};
	return json({
		uid: note.uid ?? String(note.id),
		body: note.body,
		author: note.author,
		createdAt: note.created_at
	});
};
