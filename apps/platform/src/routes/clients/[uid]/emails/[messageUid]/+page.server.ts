import { error, redirect } from '@sveltejs/kit';
import type { CommsMessage } from '@graphene/ui/comms';
import { apiUrl, authHeaders } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * One sent email, opened from inside a client's record.
 *
 * The firm-wide `/emails/{uid}` renders the same message; this exists so the RECORD survives the
 * click. Opening a message from a client's Activity tab used to land on that page, which dropped
 * the client sidebar and offered a "← Activity" back link to the whole firm's feed — from a client
 * you never chose to leave.
 *
 * IT ALSO CHECKS THE MESSAGE IS THIS CLIENT'S, which the firm-wide page has no reason to. The API
 * scopes a message to the caller's FIRM, so `/clients/{A}/emails/{a message to client B}` would
 * otherwise load and render B's subject, body and email address under A's name. That is the same
 * mistake in miniature as the unscoped Events feed this section was built alongside, so it is
 * refused here rather than left to the address bar. A mismatch is a 404, not a 403: the message
 * exists and the reader may see it — just not here — and 404 is what a wrong URL deserves.
 */
export const load: PageServerLoad = async ({ params, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { canReadActivity, clientName } = await parent();
	if (!canReadActivity) error(403, 'You do not have permission to view this client’s activity.');

	// The 403 is resolved here; the message is STREAMED so the record chrome is on screen the
	// moment the layout has named the client. A mismatch still 404s — from inside the promise.
	return {
		client: { uid: params.uid, name: clientName },
		record: loadMessage(params.messageUid, params.uid, locals.accessToken, fetch)
	};
};

async function loadMessage(
	messageUid: string,
	contactUid: string,
	accessToken: string | null,
	fetch: typeof globalThis.fetch
) {
	const res = await fetch(`${apiUrl()}/firm/comms/messages/${encodeURIComponent(messageUid)}`, {
		headers: authHeaders(accessToken)
	});
	if (res.status === 404) error(404, 'Message not found');
	if (!res.ok) error(res.status === 403 ? 403 : 502, 'Could not load this message');

	const message = (await res.json()) as CommsMessage;
	// Older rows predate `recipient_contact_uid` and carry none. Those are unattributable rather
	// than another client's, so they are refused here too — this section only ever shows mail it
	// can prove is this client's.
	if (message.recipient_contact_uid !== contactUid) error(404, 'Message not found');

	return { message };
}
