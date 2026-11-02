import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Mint a one-time DCube SSO link for this account's drawdown journey.
 *
 * The link comes from Morgan Lloyd, not from us: `cam_setmemberdetails` creates the benefit
 * application and answers with an `ssoUrl` carrying the loginId + ssoToken. Nothing is cached —
 * the token is single-use, so every open of the drawer asks for a fresh one.
 *
 * A POST rather than a GET because it *creates* a benefit application on Morgan Lloyd's side.
 */
export const POST: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	// Starting a drawdown creates a benefit application — money movement, instructions:write
	// (the API's drawdown-session endpoint demands the same slug; this keeps the proxy from
	// even forwarding an ungated call).
	if (!can(locals.permissions, locals.role, 'instructions:write')) {
		return json({ error: 'You do not have permission to start a drawdown.' }, { status: 403 });
	}

	const res = await fetch(`${apiUrl()}/crm/accounts/${params.uid}/drawdown-session`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
