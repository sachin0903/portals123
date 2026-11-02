/**
 * What conversation the reader is currently looking at — the one thing the notification bell cannot
 * work out for itself.
 *
 * A chat message arriving while its own thread is open used to pop a toast and add to the badge, so
 * you were told about a message you had just watched land. The bell has no way to know that: it
 * sits in the shell, the thread is a page inside it, and the two never meet. This is the seam
 * between them — the thread registers what it is showing, and `$lib/notifications`' predicate reads
 * it.
 *
 * A CONTACT, not a conversation uid, because the same thread is reachable from two surfaces (the
 * inbox and the client record's Messages tab) and the contact is what both of them agree on — it is
 * also what the notification itself carries (`subject_type='contact'`), so no id has to be
 * translated to make the comparison.
 *
 * Module-level rather than a context: the bell is mounted by the ROOT layout and the thread by a
 * page several layers below it, so there is no component boundary to hand a context through. Both
 * live in one browser tab, so one module-level value is exactly the right scope.
 */

/** The contact whose thread is on screen, or null. Written by the thread on mount and cleared on
 *  destroy — never left set, or navigating away would silently swallow that client's next
 *  notification. */
let openContactId = $state<number | null>(null);

/** Register the thread on screen. Returns the cleanup, so a caller can `$effect(() => reading(id))`
 *  and be unable to forget it — forgetting is the failure that hides notifications. */
export function readingContact(contactId: number): () => void {
	openContactId = contactId;
	return () => {
		// Only clear if it is still ours. Two threads never mount at once today, but a future
		// side-by-side would otherwise have the first one's teardown clear the second's claim.
		if (openContactId === contactId) openContactId = null;
	};
}

/** Whether this contact's thread is the one being read right now. */
export function isReadingContact(contactId: number): boolean {
	return openContactId === contactId;
}
