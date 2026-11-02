import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { TermItem } from '$lib/terms';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'terms:read')) {
		return { items: [] as TermItem[], canManage: false, loadError: false };
	}
	const params = new URLSearchParams();
	const q = url.searchParams.get('q')?.trim() ?? '';
	const status = url.searchParams.get('status')?.trim() ?? '';
	if (q) params.set('q', q);
	if (status) params.set('status', status);
	const qs = params.toString();
	const res = await fetchJson<TermItem[]>(
		fetch,
		`${apiUrl()}/terms${qs ? `?${qs}` : ''}`,
		authHeaders(locals.accessToken)
	);
	return {
		items: res.ok ? res.data : [],
		canManage: can(locals.permissions, locals.role, 'terms:write'),
		loadError: !res.ok,
		q,
		status
	};
};

export const actions: Actions = {
	create: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'terms:write')) {
			return fail(403, { error: 'You do not have permission to create terms.' });
		}
		const form = await request.formData();
		const title = String(form.get('title') ?? '').trim();
		if (!title) return fail(400, { error: 'Title is required.' });
		const res = await fetch(`${apiUrl()}/terms`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ title, audience: 'client', required_at: ['onboarding'] })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created = (await res.json()) as TermItem;
		redirect(303, `/terms/${created.uid}`);
	}
};
