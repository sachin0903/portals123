import { json } from '@sveltejs/kit';
import { detail } from '$lib/api';
import { canOperateWrite, targetJson, targetMutation } from '$lib/operate';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user || !canOperateWrite(locals))
		return json({ error: 'This tenant portal is read-only.' }, { status: 403 });
	const account = await targetJson<{ client: { uid: string | null } }>(
		fetch,
		locals.accessToken,
		params.uid,
		`/crm/accounts/${params.accountUid}`
	);
	if (!account.ok || !account.data.client.uid)
		return json({ error: 'This account has no client to instruct for.' }, { status: 409 });
	const response = await targetMutation(
		fetch,
		locals.accessToken,
		params.uid,
		`/firm/contacts/${account.data.client.uid}/instructions`,
		{ method: 'POST', body: JSON.stringify(await request.json()) }
	);
	if (!response.ok) return json({ error: await detail(response) }, { status: response.status });
	return json(await response.json());
};
