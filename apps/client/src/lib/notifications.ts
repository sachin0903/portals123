// Client-side helpers for the notification bell/feed — same-origin proxies (the API token lives
// server-side). Each answers whether the write LANDED: the bell and the archive apply the change
// optimistically and, on `false`, put themselves back in step (`@graphene/ui` write-outcome.ts).
// They used to be fire-and-forget; with the badge poll gone in favour of the realtime stream,
// nothing came along later to correct a refused or lost write, so the screen kept claiming it.

async function post(path: string, body: unknown): Promise<boolean> {
	try {
		const res = await fetch(path, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(body)
		});
		return res.ok;
	} catch {
		return false;
	}
}

/** Mark one notification read (`uid` given) or all of the caller's unread (`uid` omitted).
 * `contactId` narrows "mark all" to one linked contact — ignored (and unneeded) for a single
 * uid, since that row is already resolved to a specific one. */
export function markNotification(uid?: string, contactId?: number | null): Promise<boolean> {
	return post('/notifications/mark', uid ? { uid } : { contactId: contactId ?? undefined });
}

/** Dismiss one notification (remove it from the feed). */
export function dismissNotification(uid: string): Promise<boolean> {
	return post('/notifications/dismiss', { uid });
}

/** Dismiss every notification off the bell. `contactId` narrows it to one linked contact — see
 * `markNotification` for why. */
export function dismissAllNotifications(contactId?: number | null): Promise<boolean> {
	return post('/notifications/dismiss-all', { contactId: contactId ?? undefined });
}
