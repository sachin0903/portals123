import { error, json } from '@sveltejs/kit';
import { canOperateWrite, targetMutation } from '$lib/operate';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request, locals, fetch, params }) => {
	if (!canOperateWrite(locals)) error(403, 'This tenant portal is open in read-only mode.');
	const body = await request.json();
	const flow = typeof body.flow === 'string' ? body.flow : '';
	if (!flow) error(400, 'No journey specified.');
	const response = await targetMutation(
		fetch,
		locals.accessToken,
		params.uid,
		`/flows/${flow}/validate`,
		{
			method: 'POST',
			body: JSON.stringify({ definition: body.definition ?? {} })
		}
	);
	if (!response.ok) error(response.status, 'Could not check this journey.');
	return json(await response.json());
};
