import { json } from '@sveltejs/kit';
import { detail } from '$lib/api';
import { canOperateWrite, targetJson, targetMutation } from '$lib/operate';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const result = await targetJson<unknown[]>(
		fetch,
		locals.accessToken,
		params.uid,
		'/transfers/providers'
	);
	return json({ providers: result.ok ? result.data : [] });
};

export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user || !canOperateWrite(locals))
		return json({ error: 'This tenant portal is read-only.' }, { status: 403 });
	const account = await targetJson<{ id: number }>(
		fetch,
		locals.accessToken,
		params.uid,
		`/crm/accounts/${params.accountUid}`
	);
	if (!account.ok)
		return json({ error: 'Could not resolve this account.' }, { status: account.status || 502 });
	const body = { ...((await request.json()) as Record<string, unknown>) };
	delete body.account_id;
	delete body.transfer_type;
	const response = await targetMutation(fetch, locals.accessToken, params.uid, '/transfers', {
		method: 'POST',
		body: JSON.stringify({ ...body, account_id: account.data.id })
	});
	if (!response.ok) return json({ error: await detail(response) }, { status: response.status });
	return json(await response.json());
};
