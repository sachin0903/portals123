import { error, fail, redirect } from '@sveltejs/kit';
import type { ApiRequest } from '@graphene/ui/book/documents';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const result = await targetJson<ApiRequest>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/documents/requests/${params.requestUid}`
	);
	if (!result.ok) {
		if (result.status === 404) error(404, 'Request not found.');
		error(result.status || 500, 'Could not load the request.');
	}
	return { request: result.data };
};

export const actions: Actions = {
	cancel: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/documents/requests/${event.params.requestUid}/cancel`,
			{ method: 'POST' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	}
};
