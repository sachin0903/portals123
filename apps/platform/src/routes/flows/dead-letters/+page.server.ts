import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

/** `GET /flows/dead-letters` — a side-effect the engine gave up on. */
export interface DeadLetter {
	id: number;
	/** The row's address — `/flows/dead-letters/{kind}/{uid}/replay` is what a retry POSTs. */
	uid: string;
	kind: 'outbox' | 'render';
	event_type: string;
	attempts: number;
	max_attempts: number;
	last_error: string | null;
	last_attempt: string | null;
	created_at: string;
	summary: string | null;
	payload: Record<string, unknown> | null;
}

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// Whoever authors the side-effects (raise-a-task specs, generated documents) owns their failures.
	if (!can(locals.permissions, locals.role, 'flows:write')) {
		error(403, 'You do not have permission to manage flows.');
	}
	const res = await fetchJson<DeadLetter[]>(
		fetch,
		`${apiUrl()}/flows/dead-letters`,
		authHeaders(locals.accessToken)
	);
	return { rows: res.ok ? res.data : [], loadFailed: !res.ok };
};

export const actions: Actions = {
	/** Requeue one dead letter for the worker to retry. */
	replay: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'flows:write')) {
			return fail(403, { error: 'You do not have permission to manage flows.' });
		}
		const form = await request.formData();
		const kind = String(form.get('kind') ?? '');
		const uid = String(form.get('uid') ?? '');
		if (!kind || !uid) return fail(400, { error: 'No dead letter specified.' });
		const res = await fetch(`${apiUrl()}/flows/dead-letters/${kind}/${uid}/replay`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: '{}'
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
