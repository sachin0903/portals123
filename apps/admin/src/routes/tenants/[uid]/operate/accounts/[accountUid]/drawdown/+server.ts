import { json } from '@sveltejs/kit';
import { detail } from '$lib/api';
import { canOperateWrite, targetMutation } from '$lib/operate';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user || !canOperateWrite(locals))
		return json({ error: 'This tenant portal is read-only.' }, { status: 403 });
	const response = await targetMutation(
		fetch,
		locals.accessToken,
		params.uid,
		`/crm/accounts/${params.accountUid}/drawdown-session`,
		{ method: 'POST' }
	);
	if (!response.ok) return json({ error: await detail(response) }, { status: response.status });
	return json(await response.json());
};
