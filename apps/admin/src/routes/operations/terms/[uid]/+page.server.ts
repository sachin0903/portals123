import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { TermItem, TermVersion } from '$lib/terms-catalogue';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, params }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'platform_terms:read')) {
		error(403, 'Not authorised');
	}
	const [itemRes, versionsRes] = await Promise.all([
		fetch(`${apiUrl()}/terms/graphene/${params.uid}`, {
			headers: authHeaders(locals.accessToken)
		}),
		fetch(`${apiUrl()}/terms/graphene/${params.uid}/versions`, {
			headers: authHeaders(locals.accessToken)
		})
	]);
	if (!itemRes.ok) redirect(303, '/operations/terms');
	return {
		item: (await itemRes.json()) as TermItem,
		versions: versionsRes.ok ? ((await versionsRes.json()) as TermVersion[]) : [],
		canManage: can(locals.permissions, locals.role, 'platform_terms:write')
	};
};

export const actions: Actions = {
	save: async ({ request, locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'platform_terms:write')) {
			return fail(403, { error: 'You do not have permission to edit platform terms.' });
		}
		const form = await request.formData();
		const body = {
			title: String(form.get('title') ?? '').trim(),
			audience: String(form.get('audience') ?? 'all'),
			required_at: form.getAll('required_at').map(String).filter(Boolean),
			body_kind: String(form.get('body_kind') ?? 'html'),
			body_md: String(form.get('body_md') ?? ''),
			applicability: { always: true }
		};
		if (!body.required_at.length) body.required_at = ['login'];
		const res = await fetch(`${apiUrl()}/terms/graphene/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	publish: async ({ locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'platform_terms:write')) {
			return fail(403, { error: 'You do not have permission to publish platform terms.' });
		}
		const res = await fetch(`${apiUrl()}/terms/graphene/${params.uid}/publish`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	archive: async ({ locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'platform_terms:write')) {
			return fail(403, { error: 'You do not have permission to archive platform terms.' });
		}
		const res = await fetch(`${apiUrl()}/terms/graphene/${params.uid}/archive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		redirect(303, '/operations/terms');
	}
};
