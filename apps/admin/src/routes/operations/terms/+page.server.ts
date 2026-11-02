import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { TermItem } from '$lib/terms-catalogue';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'platform_terms:read')) {
		error(403, 'Not authorised');
	}
	const params = new URLSearchParams();
	const q = url.searchParams.get('q')?.trim() ?? '';
	if (q) params.set('q', q);
	const qs = params.toString();
	const res = await fetch(`${apiUrl()}/terms/graphene${qs ? `?${qs}` : ''}`, {
		headers: authHeaders(locals.accessToken)
	});
	const items: TermItem[] = res.ok ? ((await res.json()) as TermItem[]) : [];
	return {
		items,
		canManage: can(locals.permissions, locals.role, 'platform_terms:write'),
		loadError: !res.ok,
		q
	};
};

export const actions: Actions = {
	create: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'platform_terms:write')) {
			return fail(403, { error: 'You do not have permission to create platform terms.' });
		}
		const form = await request.formData();
		const title = String(form.get('title') ?? '').trim();
		if (!title) return fail(400, { error: 'Title is required.' });
		const res = await fetch(`${apiUrl()}/terms/graphene`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				title,
				audience: String(form.get('audience') ?? 'all'),
				required_at: ['login']
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created = (await res.json()) as TermItem;
		redirect(303, `/operations/terms/${created.uid}`);
	}
};
