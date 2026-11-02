import { fail, redirect } from '@sveltejs/kit';
import type { ReviewRow } from '@graphene/ui/book/reviews';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, locals, fetch, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:reviews');
	const submitted_by = url.searchParams
		.getAll('submitted_by')
		.map((v) => v.trim())
		.filter(Boolean);
	const action_key = url.searchParams.get('action')?.trim() ?? '';
	const status = url.searchParams.get('status')?.trim() ?? '';
	const q = url.searchParams.get('q')?.trim() ?? '';
	// The seat (for_me · mine · waiting · settled) lives in the URL so a link to "what is mine to
	// decide" is a link; absent, the listing picks the reader's default.
	const seat = url.searchParams.get('seat')?.trim() || null;
	const openUid = url.searchParams.get('open')?.trim() || null;
	const res = await fetchJson<ReviewRow[]>(
		fetch,
		`${apiUrl()}/reviews?scope=all`,
		authHeaders(locals.accessToken)
	);
	return {
		reviews: res.ok ? res.data : [],
		loadError: !res.ok,
		filters: { submitted_by, action_key, status, q, seat },
		openUid
	};
};

async function decideAction(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	uid: string,
	decision: 'approve' | 'reject',
	note?: string
) {
	const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(uid)}/decision`, {
		method: 'POST',
		headers: jsonHeaders(accessToken),
		body: JSON.stringify({ decision, ...(note ? { note } : {}) })
	});
	if (!res.ok) return fail(res.status, { error: await detail(res) });
	return { ok: true };
}

export const actions: Actions = {
	approve: async ({ request, locals, fetch }) => {
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No review specified.' });
		if (String(form.get('source') ?? '') === 'action') {
			return decideAction(fetch, locals.accessToken, uid, 'approve');
		}
		const res = await fetch(`${apiUrl()}/reviews/${uid}/approve`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	reject: async ({ request, locals, fetch }) => {
		const data = await request.formData();
		const uid = String(data.get('uid') ?? '').trim();
		const note = String(data.get('note') ?? '').trim();
		if (!uid) return fail(400, { error: 'No review specified.' });
		if (String(data.get('source') ?? '') === 'action') {
			if (!note) return fail(422, { error: 'Say why you are rejecting this.', field: 'note' });
			return decideAction(fetch, locals.accessToken, uid, 'reject', note);
		}
		if (!note) return fail(422, { error: 'Say why you are rejecting this.', field: 'note' });
		const res = await fetch(`${apiUrl()}/reviews/${uid}/reject`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ note })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	cancel: async ({ request, locals, fetch }) => {
		const uid = String((await request.formData()).get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No review specified.' });
		const res = await fetch(`${apiUrl()}/reviews/${uid}/cancel`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	// The person who submitted a case nudges its approvers: the same notice again, once an hour.
	remind: async ({ request, locals, fetch }) => {
		const uid = String((await request.formData()).get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No review specified.' });
		const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(uid)}/remind`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
