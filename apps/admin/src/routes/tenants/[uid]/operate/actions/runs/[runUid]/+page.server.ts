import { error, fail, redirect } from '@sveltejs/kit';
import type { RunDetailData } from '@graphene/ui';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, params, parent, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:actions');
	const { tenant, writing } = await parent();
	const res = await targetJson<RunDetailData>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/actions/runs/${encodeURIComponent(params.runUid)}`
	);
	if (!res.ok) error(res.status || 404, 'That run was not found.');
	return { run: res.data, canManage: writing };
};

export const actions: Actions = {
	// Retry the run from where it stopped, or one node of it (a node is a run).
	retry: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const uid = String(data.get('step_id') ?? data.get('run_uid') ?? params.runUid).trim();
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/actions/runs/${encodeURIComponent(uid)}/retry`,
			{ method: 'POST' }
		);
		if (!response.ok) return mutationFailure(response, 'Could not retry this run.');
		return { ok: true };
	},
	cancel: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const note = String(data.get('note') ?? '').trim() || null;
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/actions/runs/${encodeURIComponent(params.runUid)}/cancel`,
			{ method: 'POST', body: JSON.stringify({ note }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not cancel this run.');
		return { ok: true };
	},
	decide: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const runUid = String(data.get('run_uid') ?? params.runUid).trim();
		const decision = String(data.get('decision') ?? '').trim();
		const note = String(data.get('note') ?? '').trim() || null;
		const isReview = data.get('is_review') === 'true';
		if (decision !== 'approve' && decision !== 'reject') {
			return fail(400, { error: 'Choose a decision.' });
		}
		const path = isReview ? `/reviews/${runUid}/${decision}` : `/actions/runs/${runUid}/decision`;
		const body = isReview ? { note } : { decision, note };
		const response = await targetMutation(fetch, locals.accessToken, params.uid, path, {
			method: 'POST',
			body: JSON.stringify(body)
		});
		if (!response.ok) return mutationFailure(response, 'Could not record this decision.');
		return { ok: true };
	}
};
