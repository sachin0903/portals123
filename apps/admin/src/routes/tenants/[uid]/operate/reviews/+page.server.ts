import { fail, redirect } from '@sveltejs/kit';
import type { ReviewRow } from '@graphene/ui/book/reviews';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, locals, fetch, parent, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:reviews');
	const { tenant, writing } = await parent();
	const submitted_by = url.searchParams
		.getAll('submitted_by')
		.map((v) => v.trim())
		.filter(Boolean);
	const action_key = url.searchParams.get('action')?.trim() ?? '';
	const status = url.searchParams.get('status')?.trim() ?? '';
	const q = url.searchParams.get('q')?.trim() ?? '';
	const seat = url.searchParams.get('seat')?.trim() || null;
	const openUid = url.searchParams.get('open')?.trim() || null;
	const result = await targetJson<ReviewRow[]>(fetch, locals.accessToken, tenant.uid, '/reviews', {
		scope: 'all'
	});
	return {
		reviews: result.ok ? result.data : [],
		loadError: !result.ok,
		filters: { submitted_by, action_key, status, q, seat },
		openUid,
		canApprove: writing
	};
};

export const actions: Actions = {
	approve: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No review specified.' });
		// An action-sourced hold decides through baton so its run lifecycle fires; a book review
		// through the /reviews path where its apply lives.
		const isAction = String(form.get('source') ?? '') === 'action';
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			isAction ? `/actions/runs/${uid}/decision` : `/reviews/${uid}/approve`,
			{ method: 'POST', body: JSON.stringify(isAction ? { decision: 'approve' } : {}) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not approve this review.');
		return { ok: true };
	},

	reject: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const data = await request.formData();
		const uid = String(data.get('uid') ?? '').trim();
		const note = String(data.get('note') ?? '').trim();
		if (!uid) return fail(400, { error: 'No review specified.' });
		if (!note) return fail(422, { error: 'Say why you are rejecting this.', field: 'note' });
		const isAction = String(data.get('source') ?? '') === 'action';
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			isAction ? `/actions/runs/${uid}/decision` : `/reviews/${uid}/reject`,
			{ method: 'POST', body: JSON.stringify(isAction ? { decision: 'reject', note } : { note }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not reject this review.');
		return { ok: true };
	},

	cancel: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const uid = String((await request.formData()).get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No review specified.' });
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/reviews/${uid}/cancel`,
			{ method: 'POST' }
		);
		if (!response.ok) return mutationFailure(response, 'Could not cancel this review.');
		return { ok: true };
	}
};
