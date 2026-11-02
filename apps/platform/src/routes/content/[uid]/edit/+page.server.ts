import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { ContentCategory, ContentItem } from '$lib/content';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, params }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'content:read')) redirect(303, '/content');
	const headers = authHeaders(locals.accessToken);
	const [itemRes, catsRes] = await Promise.all([
		fetchJson<ContentItem>(fetch, `${apiUrl()}/content/items/${params.uid}`, headers),
		fetchJson<ContentCategory[]>(fetch, `${apiUrl()}/content/categories`, headers)
	]);
	if (!itemRes.ok) throw error(itemRes.status === 404 ? 404 : 502, 'Content not found');
	return {
		item: itemRes.data,
		categories: catsRes.ok ? catsRes.data : [],
		canManage: can(locals.permissions, locals.role, 'content:write')
	};
};

function requireManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'content:write')) redirect(303, '/content');
}

function csv(form: FormData, key: string): string[] {
	return String(form.get(key) ?? '')
		.split(',')
		.map((s) => s.trim())
		.filter(Boolean);
}

export const actions: Actions = {
	save: async ({ params, request, locals, fetch }) => {
		requireManage(locals);
		const form = await request.formData();
		const expiresRaw = String(form.get('expires_at') ?? '').trim();
		const category = String(form.get('category_uid') ?? '');
		const schemaHtml = form.get('html');
		const schemaCss = form.get('css');
		const body: Record<string, unknown> = {
			title: String(form.get('title') ?? ''),
			summary: String(form.get('summary') ?? ''),
			audience: String(form.get('audience') ?? 'all'),
			service_types: csv(form, 'service_types'),
			sub_types: csv(form, 'sub_types'),
			category_uid: category,
			clear_expires: !expiresRaw
		};
		if (expiresRaw) body.expires_at = new Date(expiresRaw).toISOString();
		if (typeof schemaHtml === 'string') {
			body.schema = { html: schemaHtml, css: String(schemaCss ?? '') };
		}
		const res = await fetch(`${apiUrl()}/content/items/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { action: 'save', error: await detail(res) });
		return { action: 'save', ok: true };
	},

	publish: async ({ params, locals, fetch }) => {
		requireManage(locals);
		const res = await fetch(`${apiUrl()}/content/items/${params.uid}/publish`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'publish', error: await detail(res) });
		return { action: 'publish', ok: true };
	},

	archive: async ({ params, locals, fetch }) => {
		requireManage(locals);
		const res = await fetch(`${apiUrl()}/content/items/${params.uid}/archive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'archive', error: await detail(res) });
		redirect(303, '/content');
	},

	createCategory: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const name = String((await request.formData()).get('name') ?? '').trim();
		if (!name) return fail(400, { action: 'createCategory', error: 'Name is required.' });
		const res = await fetch(`${apiUrl()}/content/categories`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ name })
		});
		if (!res.ok) return fail(res.status, { action: 'createCategory', error: await detail(res) });
		return { action: 'createCategory', ok: true };
	}
};
