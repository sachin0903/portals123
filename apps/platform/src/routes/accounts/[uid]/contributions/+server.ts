import { json } from '@sveltejs/kit';
import { accountIdFrom, apiUrl, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Book a contribution against this account.
 *
 * A POST because it creates three things: the contribution record, the payment expectation, and
 * — best-effort — an Anticipated Cash Receipt at the custodian. The API answers with the created
 * contribution plus a `sei` block reporting whether that last step landed; the drawer shows it
 * rather than pretending the custodian was told when it wasn't.
 *
 * `account_id` comes from the route, never the body: the drawer is always opened from one
 * account's page, and taking it from the client would let a crafted request book against another.
 */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });

	const body = (await request.json()) as Record<string, unknown>;
	// `account_id` travels in the BODY, which is not a URL — so this is the one place the
	// account's serial is still needed, and it is read off the record at its uid.
	const accountId = await accountIdFrom(fetch, locals.accessToken, params.uid);
	const res = await fetch(`${apiUrl()}/contributions`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ ...body, account_id: accountId })
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
