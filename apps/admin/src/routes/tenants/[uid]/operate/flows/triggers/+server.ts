import { error, json } from '@sveltejs/kit';
import type { AutomationRow } from '@graphene/ui/automations';
import { canOperateWrite, targetJson, targetMutation } from '$lib/operate';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url, locals, fetch, params }) => {
	const flow = url.searchParams.get('flow') ?? '';
	if (!flow) error(400, 'No journey specified.');
	const result = await targetJson<AutomationRow[]>(
		fetch,
		locals.accessToken,
		params.uid,
		'/automations'
	);
	// `|| 502`: a `targetJson` that never reached the API reports status 0, and `error(0)` throws
	// inside SvelteKit — an unreachable API would be reported as a fault in this handler.
	if (!result.ok) error(result.status || 502, 'Could not load this journey’s triggers.');
	return json({
		automations: result.data.filter((automation) => automation.flow_uid === flow),
		canWrite: canOperateWrite(locals),
		permitted: true
	});
};

export const POST: RequestHandler = async ({ request, locals, fetch, params }) => {
	if (!canOperateWrite(locals)) error(403, 'This tenant portal is open in read-only mode.');
	const response = await targetMutation(fetch, locals.accessToken, params.uid, '/automations', {
		method: 'POST',
		body: JSON.stringify(await request.json())
	});
	if (!response.ok) error(response.status, 'That trigger could not be armed.');
	return json(await response.json());
};

export const PATCH: RequestHandler = async ({ request, locals, fetch, params }) => {
	if (!canOperateWrite(locals)) error(403, 'This tenant portal is open in read-only mode.');
	const { uid, ...body } = await request.json();
	if (!uid) error(400, 'No automation specified.');
	const response = await targetMutation(
		fetch,
		locals.accessToken,
		params.uid,
		`/automations/${uid}`,
		{
			method: 'PATCH',
			body: JSON.stringify(body)
		}
	);
	if (!response.ok) error(response.status, 'That trigger could not be changed.');
	return json(await response.json());
};

export const DELETE: RequestHandler = async ({ url, locals, fetch, params }) => {
	if (!canOperateWrite(locals)) error(403, 'This tenant portal is open in read-only mode.');
	const uid = url.searchParams.get('uid') ?? '';
	const response = await targetMutation(
		fetch,
		locals.accessToken,
		params.uid,
		`/automations/${uid}`,
		{
			method: 'DELETE'
		}
	);
	if (!response.ok) error(response.status, 'That trigger could not be removed.');
	return json({ ok: true });
};
