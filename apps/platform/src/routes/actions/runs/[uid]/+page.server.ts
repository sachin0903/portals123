import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { RunDetailData } from '@graphene/ui';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, params, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:actions');
	const allowed =
		can(locals.permissions, locals.role, 'actions:read') ||
		can(locals.permissions, locals.role, 'actions:write');
	if (!allowed) error(403, 'You do not have permission to view actions.');
	const res = await fetchJson<RunDetailData>(
		fetch,
		`${apiUrl()}/actions/runs/${encodeURIComponent(params.uid)}`,
		authHeaders(locals.accessToken)
	);
	if (!res.ok) error(res.status || 404, 'That run was not found.');
	return { run: res.data, canManage: can(locals.permissions, locals.role, 'actions:write') };
};

export const actions: Actions = {
	// Retry the run from where it stopped, or one node of it. A node is a run, so both post to the
	// same endpoint with the uid in hand (`step_id` from a node's own Retry, else this run).
	retry: async ({ request, locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'actions:write')) {
			return fail(403, { error: 'You cannot retry this run.' });
		}
		const data = await request.formData();
		const uid = String(data.get('step_id') ?? data.get('run_uid') ?? params.uid).trim();
		const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(uid)}/retry`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	cancel: async ({ request, locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'actions:write')) {
			return fail(403, { error: 'You cannot cancel this run.' });
		}
		const data = await request.formData();
		const note = String(data.get('note') ?? '').trim() || null;
		const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(params.uid)}/cancel`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ note })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	// Approve or reject the held run (or a gated child of it). The API enforces who may decide.
	decide: async ({ request, locals, fetch, params }) => {
		const data = await request.formData();
		const runUid = String(data.get('run_uid') ?? params.uid).trim();
		const decision = String(data.get('decision') ?? '').trim();
		const note = String(data.get('note') ?? '').trim() || null;
		const isReview = data.get('is_review') === 'true';
		if (decision !== 'approve' && decision !== 'reject') {
			return fail(400, { error: 'Choose a decision.' });
		}
		const url = isReview
			? `${apiUrl()}/reviews/${runUid}/${decision}`
			: `${apiUrl()}/actions/runs/${runUid}/decision`;
		const body = isReview ? { note } : { decision, note };
		const res = await fetch(url, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
