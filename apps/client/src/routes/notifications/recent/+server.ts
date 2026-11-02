import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { activeContactId } from '$lib/active-contact';

/** How many rows the bell's dropdown can show. It renders `max` (8 by default) and this is the
 *  pool it slices — a little headroom costs nothing and spares a refetch when items are dismissed. */
const RECENT_LIMIT = 20;

/**
 * Same-origin proxy for the bell's FEED refetch — the rows, not the badge.
 *
 * The dropdown's items arrive from the root layout's `load`, which SvelteKit never re-runs on a
 * client-side navigation (no url/params dependency, nothing calls `invalidate()` for it). So without
 * this the list was frozen at the last FULL page load while the badge beside it updated live off
 * `unread-count` — the bell announced notifications that could not be read until you reloaded.
 *
 * The bell calls this when its popover opens and whenever the realtime socket announces a delivery,
 * and again with `before_uid` (forwarded as-is) when "Load more" pages further back through the
 * keyset cursor. `include_dismissed` is deliberately never forwarded — the bell is exactly the
 * surface a dismissal is supposed to hide a row from; the full archive lives on the
 * all-notifications page, which is not this proxy.
 *
 * A failure answers with an empty list and a non-2xx is treated as "leave the rows alone" by the
 * caller, so a broken refetch degrades to stale rows rather than an empty dropdown.
 *
 * `?contact_id=` is forwarded, and the layout always sets it for a login linked to more than one
 * client. Without it this call is scoped by RLS alone, which is EVERY linked contact — so the
 * dropdown merged all of them while the badge next to it counted only the active one. The two other
 * reads of this feed (the layout's own seed and the all-notifications page) were already narrowed;
 * this was the one that was not, so opening the bell silently widened it.
 */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ items: [] }, { status: 401 });
	const params = new URLSearchParams({ limit: String(RECENT_LIMIT) });
	const beforeUid = url.searchParams.get('before_uid');
	if (beforeUid) params.set('before_uid', beforeUid);
	const contactId = activeContactId(url);
	if (contactId !== null) params.set('contact_id', String(contactId));
	const res = await fetch(`${apiUrl()}/notifications?${params}`, {
		headers: authHeaders(locals.accessToken)
	});
	// Forward the failure rather than dressing it as an empty feed: the bell keeps what it has.
	if (!res.ok) return json({ items: [] }, { status: 502 });
	const body = await res.json();
	return json({ items: Array.isArray(body) ? body : (body?.items ?? []) });
};
