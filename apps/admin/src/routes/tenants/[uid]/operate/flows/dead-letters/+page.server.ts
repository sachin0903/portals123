import { redirect } from '@sveltejs/kit';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export type DeadLetter = {
	id: number;
	uid: string;
	kind: 'outbox' | 'render';
	event_type: string;
	attempts: number;
	max_attempts: number;
	last_error: string | null;
	created_at: string;
	summary: string | null;
};
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const result = await targetJson<DeadLetter[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/flows/dead-letters'
	);
	return { rows: result.ok ? result.data : [], loadFailed: !result.ok, canReplay: writing };
};
export const actions: Actions = {
	replay: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/flows/dead-letters/${form.get('kind')}/${form.get('uid')}/replay`,
			{ method: 'POST', body: '{}' }
		);
		if (!response.ok) return mutationFailure(response, 'Could not replay the side-effect.');
		return { ok: true };
	}
};
