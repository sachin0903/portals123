import { json, type RequestHandler } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders, detail } from '$lib/api';

/**
 * Withdraw a note from the conversation it was written beside — the other half of the sibling
 * `POST`, so a note taken in the rail can be taken back in the rail.
 *
 * The SAME endpoint the record's `deleteNote` action calls (`DELETE /firm/contacts/{uid}/notes/
 * {note_uid}`), gated on the same `contacts:write`. The API keeps the delete to the CALLER'S OWN
 * notes in its WHERE clause, so this passes the uid and lets the server decide — a colleague's
 * note answers 404, and that is reported rather than guessed at from a display name.
 */
export const DELETE: RequestHandler = async ({ locals, fetch, params }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'contacts:write')) {
		return json({ error: 'You cannot delete notes on this client.' }, { status: 403 });
	}

	const res = await fetch(
		`${apiUrl()}/firm/contacts/${params.contactUid}/notes/${params.noteUid}`,
		{ method: 'DELETE', headers: jsonHeaders(locals.accessToken) }
	);
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json({ note_uid: params.noteUid });
};
