import { isReadingContact } from './reading.svelte';

// Client-side helpers for the notification bell/feed. They hit THIS app's same-origin proxies
// (the browser has no API token — the sealed session lives server-side), which forward to the API
// with `locals.accessToken`. Fire-and-forget: the bell/feed update optimistically, and the badge
// self-heals on its next poll if a write is lost.

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

/**
 * Whether the reader is already looking at what a notification is about.
 *
 * Only chat messages, and only the thread ON SCREEN. A message arriving in the thread you are
 * reading used to pop a toast and add to the badge, telling you about something you had just
 * watched land — and leaving it unread claimed something was waiting when nothing was. The bell
 * marks these read instead of popping them.
 *
 * Narrow on purpose. `category === 'messages'` is the only family where "on screen" is a thing this
 * app can answer; widening it to documents or tasks would mean suppressing a notification because
 * some list happens to contain the row, which is not the same as having read it.
 *
 * `subject_id` is the contact's serial id as a STRING (notifications carry subject ids as text),
 * so it is parsed rather than compared — `'42' === 42` is false and would have silently suppressed
 * nothing at all, which is the failure that looks like the feature simply not being wired.
 */
export function isOnScreen(item: {
	category: string;
	subject_type: string | null;
	subject_id: string | null;
}): boolean {
	if (item.category !== 'messages' || item.subject_type !== 'contact') return false;
	const contactId = Number(item.subject_id);
	return Number.isFinite(contactId) && isReadingContact(contactId);
}
