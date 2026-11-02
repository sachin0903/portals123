import { fail, redirect } from '@sveltejs/kit';
import type { FlowOut } from '@graphene/ui/book/journeys';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const result = await targetJson<FlowOut[]>(fetch, locals.accessToken, tenant.uid, '/flows');
	return { flows: result.ok ? result.data : [], canManage: writing, loadError: !result.ok };
};

export const actions: Actions = {
	create: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const name = String(form.get('name') ?? '').trim();
		const code = String(form.get('code') ?? '').trim();
		if (!name || !code) return fail(400, { error: 'Name and reference code are required.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/flows',
			{
				method: 'POST',
				body: JSON.stringify({
					name,
					code,
					description: String(form.get('description') ?? '') || null,
					entity: String(form.get('entity') ?? '') || null
				})
			}
		);
		if (!response.ok) return mutationFailure(response, 'Could not create the flow.');
		return { ok: true, uid: ((await response.json()) as FlowOut).uid };
	},
	setStatus: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const uid = String(form.get('uid') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/flows/${uid}`,
			{
				method: 'PATCH',
				body: JSON.stringify({ status: String(form.get('status') ?? '') })
			}
		);
		if (!response.ok) return mutationFailure(response, 'Could not update the flow.');
		return { ok: true };
	}
};
