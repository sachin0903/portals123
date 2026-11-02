import { error, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { CommsMessage } from '@graphene/ui/comms';
import { apiUrl, authHeaders } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * One sent email: its envelope, delivery timeline and body.
 *
 * A real page rather than a pane on the listing — the body is the substance and wants the width,
 * and a message is a thing worth linking someone to.
 *
 * The uid is scoped to the session's firm by the API, which 404s a uid from another firm's ledger.
 * That is the same answer as "no such message" and deliberately so: distinguishing them would
 * confirm the message exists.
 */
export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'activity:read')) error(403, 'Not authorised');

	const res = await fetch(`${apiUrl()}/firm/comms/messages/${encodeURIComponent(params.uid)}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (res.status === 404) error(404, 'Message not found');
	if (!res.ok) error(res.status === 403 ? 403 : 502, 'Could not load this message');

	return { message: (await res.json()) as CommsMessage };
};
