import { fail, redirect } from '@sveltejs/kit';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';
type Item = {
	uid: string;
	kind: string;
	title: string;
	summary: string | null;
	status: string;
	audience: string;
	service_types: string[];
	sub_types: string[];
	expires_at: string | null;
	hero_url: string | null;
	has_file: boolean;
	category_uid: string | null;
	schema?: { html?: string; css?: string };
	file_content_type?: string | null;
};
type Category = { uid: string; name: string };
export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [item, categories] = await Promise.all([
		targetJson<Item>(fetch, locals.accessToken, tenant.uid, `/content/items/${params.contentUid}`),
		targetJson<Category[]>(fetch, locals.accessToken, tenant.uid, '/content/categories')
	]);
	return {
		tenantUid: tenant.uid,
		item: item.ok ? item.data : null,
		categories: categories.ok ? categories.data : [],
		canManage: writing,
		loadError: !item.ok || !categories.ok
	};
};
type ActionEvent = Parameters<NonNullable<Actions['save']>>[0];

async function mutate(e: ActionEvent, path: string, method: string, body?: unknown) {
	if (!canOperateWrite(e.locals)) return readOnlyFailure();
	const response = await targetMutation(
		e.fetch,
		e.locals.accessToken,
		e.params.uid,
		`/content/items/${e.params.contentUid}${path}`,
		{ method, body: body === undefined ? undefined : JSON.stringify(body) }
	);
	if (!response.ok) return mutationFailure(response, 'Could not update content.');
	return { ok: true };
}
export const actions: Actions = {
	save: async (e) => {
		const f = await e.request.formData();
		const html = f.get('html');
		return mutate(e, '', 'PATCH', {
			title: String(f.get('title') ?? ''),
			summary: String(f.get('summary') ?? ''),
			audience: String(f.get('audience') ?? 'all'),
			service_types: String(f.get('service_types') ?? '')
				.split(',')
				.filter(Boolean),
			sub_types: String(f.get('sub_types') ?? '')
				.split(',')
				.filter(Boolean),
			category_uid: String(f.get('category_uid') ?? ''),
			clear_expires: !f.get('expires_at'),
			...(html !== null ? { schema: { html: String(html), css: String(f.get('css') ?? '') } } : {})
		});
	},
	publish: (e) => mutate(e, '/publish', 'POST'),
	archive: (e) => mutate(e, '/archive', 'POST'),
	createCategory: async (e) => {
		if (!canOperateWrite(e.locals)) return readOnlyFailure();
		const name = String((await e.request.formData()).get('name') ?? '').trim();
		if (!name) return fail(400, { error: 'Name is required.' });
		const response = await targetMutation(
			e.fetch,
			e.locals.accessToken,
			e.params.uid,
			'/content/categories',
			{ method: 'POST', body: JSON.stringify({ name }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not create category.');
		return { ok: true };
	}
};
