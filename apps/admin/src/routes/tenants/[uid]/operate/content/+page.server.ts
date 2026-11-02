import { fail, redirect } from '@sveltejs/kit';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';
export type ContentItem = {
	uid: string;
	kind: 'news' | 'article' | 'guide';
	title: string;
	summary: string | null;
	status: string;
	updated_at: string;
};
export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const q = Object.fromEntries(
		['q', 'kind', 'status'].flatMap((k) =>
			url.searchParams.get(k) ? [[k, url.searchParams.get(k)!]] : []
		)
	);
	const result = await targetJson<ContentItem[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/content/items',
		q
	);
	return {
		tenantUid: tenant.uid,
		items: result.ok ? result.data : [],
		canManage: writing,
		loadError: !result.ok
	};
};
export const actions: Actions = {
	create: async ({ locals, fetch, params, request }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const f = await request.formData();
		const title = String(f.get('title') ?? '').trim();
		if (!title) return fail(400, { error: 'Title is required.' });
		const response = await targetMutation(fetch, locals.accessToken, params.uid, '/content/items', {
			method: 'POST',
			body: JSON.stringify({ title, kind: String(f.get('kind') ?? 'article') })
		});
		if (!response.ok) return mutationFailure(response, 'Could not create content.');
		const item = (await response.json()) as ContentItem;
		redirect(303, `/tenants/${params.uid}/operate/content/${item.uid}/edit`);
	}
};
