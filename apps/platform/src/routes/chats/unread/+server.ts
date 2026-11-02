import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';

/** Same-origin proxy for the Messages nav dot's poll (same pattern as the notification bell's
 * unread-count route) — the browser can't call the API directly, so this forwards with the
 * session token. Defaults to `false` on any failure: an outage must read as "nothing new", never
 * as a stuck dot nobody can clear.
 *
 * An optional `?contact_id=` scopes the check to one client's own conversation — the root layout's
 * poller for a focused client record's Messages dot (`sidebarFocus.liveUnreadContactId`) uses this
 * same route rather than a second endpoint. Omitted, it answers for the signed-in user overall,
 * same as before. */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ unread: false });
	const contactId = url.searchParams.get('contact_id');
	const qs = contactId ? `?contact_id=${encodeURIComponent(contactId)}` : '';
	const res = await fetch(`${apiUrl()}/messaging/unread${qs}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ unread: false });
	return json(await res.json());
};
