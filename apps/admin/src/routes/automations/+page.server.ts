import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** What `GET /admin/automations/pause` returns. */
export type PlatformPause = {
	paused: boolean;
	reason: string | null;
	paused_at: string | null;
	paused_by: string | null;
	paused_by_name: string | null;
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The read is gated on the same slug as the write, and so is this page: an operator who cannot
	// press the switch has no business reading "the estate has been stopped since 03:12, because…".
	if (!can(locals.permissions, locals.role, 'automations:global_pause'))
		error(403, 'Not authorised');

	let state: PlatformPause | null = null;
	let loadError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/admin/automations/pause`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) state = await res.json();
		else loadError = await detail(res);
	} catch {
		loadError = 'The service is unavailable.';
	}

	return { state, loadError };
};

export const actions: Actions = {
	/**
	 * Flip the switch. One action for both directions: the pause and the resume are the same
	 * decision seen from two sides, and splitting them would let the two drift on gating.
	 */
	setPause: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'automations:global_pause'))
			error(403, 'Not authorised');

		const form = await request.formData();
		const paused = String(form.get('paused') ?? '') === 'true';
		const reason = String(form.get('reason') ?? '').trim();
		// Enforced here rather than only in the UI: an incident switch with no note is the one the
		// next operator daren't touch. Resuming needs no note — the note it clears was the incident.
		if (paused && !reason)
			return fail(400, { error: 'Say why you are stopping automations.', paused, reason });

		const res = await fetch(`${apiUrl()}/admin/automations/pause`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ paused, reason: paused ? reason : null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res), paused, reason });
		return { paused };
	}
};
