import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { activeContactParam } from '$lib/active-contact';

/** Same-origin proxy for the bell's badge poll (the access token is server-side). Forwards
 * `?contact_id=`, set by the layout on the poll URL for a login linked to more than one
 * contact, so the badge agrees with the notifications page's own narrowed count. */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	// 401, not `{count: 0}`: a signed-out poll used to read as "no notifications", so a bell whose
	// session had expired sat quietly at zero instead of the badge going stale (which the bell
	// treats as "keep what you have") until the next navigation sent the reader to sign in.
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const cid = activeContactParam(url);
	const res = await fetch(`${apiUrl()}/notifications/unread-count${cid ? `?${cid}` : ''}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ count: 0 });
	return json(await res.json());
};
