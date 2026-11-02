import { json } from '@sveltejs/kit';
import { accountIdFrom, apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Raise a transfer in against this account, and list the ceding providers to pick from.
 *
 * The sibling of `../contributions/+server.ts`, and the same rule about `account_id`: it comes
 * from the route, never the body. The drawer is always opened from one account's page, and
 * taking it from the client would let a crafted request raise a transfer against another.
 *
 * The account is also what determines the transfer TYPE (pension / ISA / GIA), the base currency
 * and the custodian — so the drawer asks for none of those. Cash vs in-specie, and the extras the
 * account's counterparties take, are asked in the drawer and forwarded here. `transfer_type` is
 * deliberately not forwarded even if a caller supplies one: the API derives it from the account's
 * wrapper.
 */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });

	const res = await fetch(`${apiUrl()}/transfers/providers`, {
		headers: authHeaders(locals.accessToken)
	});
	// A firm with no register is a normal state, not a failure — the combobox accepts a typed
	// name, so an empty list still leaves the drawer usable.
	if (!res.ok) return json({ providers: [] });
	return json({ providers: await res.json() });
};

export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });

	const body = (await request.json()) as Record<string, unknown>;
	// `account_id` travels in the BODY, which is not a URL — so this is the one place the
	// account's serial is still needed, and it is read off the record at its uid.
	const accountId = await accountIdFrom(fetch, locals.accessToken, params.uid);
	// Drop both rather than destructure-and-ignore (eslint rightly objects to unused bindings):
	// `account_id` comes from the route, and `transfer_type` is derived by the API from the
	// account's wrapper. A client-supplied value for either would be a way past both rules.
	const rest = { ...body };
	delete rest.transfer_type;
	delete rest.account_id;
	const res = await fetch(`${apiUrl()}/transfers`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ ...rest, account_id: accountId })
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
