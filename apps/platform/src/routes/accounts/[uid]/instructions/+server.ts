import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user || !can(locals.permissions, locals.role, 'instructions:write'))
		return json({ error: 'You do not have permission to raise instructions.' }, { status: 403 });

	const accountResponse = await fetch(`${apiUrl()}/crm/accounts/${params.uid}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!accountResponse.ok)
		return json({ error: 'Could not resolve this account.' }, { status: accountResponse.status });
	const account = await accountResponse.json();
	if (account.client?.id == null)
		return json({ error: 'This account has no client to instruct for.' }, { status: 409 });

	const response = await fetch(`${apiUrl()}/firm/contacts/${account.client.uid}/instructions`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(await request.json())
	});
	const body = await response.json().catch(() => ({}));
	return json(response.ok ? body : { error: body.detail ?? 'Could not raise the instruction.' }, {
		status: response.status
	});
};
