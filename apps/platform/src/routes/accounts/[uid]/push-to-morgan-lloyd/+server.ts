import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Push this account to Morgan Lloyd: the adviser, the client, the scheme, the investment and
 * the beneficiaries.
 *
 * A POST because it CREATES records in a real back-office system. Safe to press twice: every
 * step re-reads its recorded identifier first and reports `skipped`, which is what stops a retry
 * putting a second human being in the CRM.
 *
 * Gated `contacts:sync_morgan_lloyd` rather than `contacts:write` or `instructions:write`,
 * matching the API: minting a person and a scheme in a live CRM is neither editing a client
 * record nor money movement. The proxy checks it so a book editor without the grant is refused
 * here rather than forwarded to be refused there.
 */
export const POST: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'contacts:sync_morgan_lloyd')) {
		return json(
			{ error: 'You cannot sync this account with Morgan Lloyd. Ask an administrator for access.' },
			{ status: 403 }
		);
	}

	const res = await fetch(`${apiUrl()}/crm/accounts/${params.uid}/sync-to-morgan-lloyd`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
