import { fail, redirect } from '@sveltejs/kit';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { TermItem } from '$lib/terms-catalogue';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const q = Object.fromEntries(
		['q', 'status'].flatMap((k) => (url.searchParams.get(k) ? [[k, url.searchParams.get(k)!]] : []))
	);
	const result = await targetJson<TermItem[]>(fetch, locals.accessToken, tenant.uid, '/terms', q);
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
		const title = String((await request.formData()).get('title') ?? '').trim();
		if (!title) return fail(400, { error: 'Title is required.' });
		const response = await targetMutation(fetch, locals.accessToken, params.uid, '/terms', {
			method: 'POST',
			body: JSON.stringify({ title, audience: 'client', required_at: ['onboarding'] })
		});
		if (!response.ok) return mutationFailure(response, 'Could not create terms.');
		const item = (await response.json()) as TermItem;
		redirect(303, `/tenants/${params.uid}/operate/terms/${item.uid}`);
	}
};
