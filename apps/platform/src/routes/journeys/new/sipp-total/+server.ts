import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson, type ContactShell } from '$lib/api';
import { existingSippTotal } from '$lib/illustrations/client';
import type { RequestHandler } from './$types';

/** The owner's other SIPP accounts, summed, for the illustration drawer.
 *  Loaded when the drawer opens, not with the applicants catalogue: a new client has none,
 *  and the applicants list does not carry account values. */
export const GET: RequestHandler = async ({ locals, fetch, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'cases:write')) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const uid = url.searchParams.get('uid')?.trim();
	if (!uid) return json({ total: null });
	const shell = await fetchJson<ContactShell>(
		fetch,
		`${apiUrl()}/crm/contacts/${encodeURIComponent(uid)}/shell`,
		authHeaders(locals.accessToken)
	);
	// A failed read is not "no SIPPs". The drawer treats a null total as nothing held and would
	// illustrate a client who already has a pension as if they did not.
	if (!shell.ok) return json({ error: 'The client’s SIPPs could not be read.' }, { status: 502 });
	return json({ total: existingSippTotal(shell.data.accounts) });
};
