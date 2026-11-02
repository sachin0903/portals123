// Client-side helpers for the notification bell/feed — same-origin proxies (the API token lives
// server-side). Fire-and-forget; the UI updates optimistically and the badge self-heals on poll.

/** Mark one notification read (`uid` given) or all of the caller's unread (`uid` omitted). */
export async function markNotification(uid?: string): Promise<void> {
	try {
		await fetch('/notifications/mark', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(uid ? { uid } : {})
		});
	} catch {
		// Optimistic UI already reflects the change; a lost write is corrected by the next poll.
	}
}

/** Dismiss one notification (remove it from the feed). Optimistic; a lost write self-corrects. */
export async function dismissNotification(uid: string): Promise<void> {
	try {
		await fetch(`/notifications/dismiss`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ uid })
		});
	} catch {
		// Optimistic UI already removed it; a lost write reappears on the next load.
	}
}

/** Dismiss every notification off the bell. Optimistic; a lost write reappears on the next load. */
export async function dismissAllNotifications(): Promise<void> {
	try {
		await fetch('/notifications/dismiss-all', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({})
		});
	} catch {
		// Optimistic UI already removed the rows; a lost write reappears on the next load.
	}
}
