import { fail, redirect } from '@sveltejs/kit';
import type { Form } from '@graphene/ui';
import { toForm, type ApiForm } from '@graphene/ui/form-builder';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const result = await targetJson<ApiForm[]>(fetch, locals.accessToken, tenant.uid, '/forms', {
		include_archived: true
	});
	return {
		forms: result.ok ? result.data.map(toForm) : ([] as Form[]),
		canManage: writing,
		loadError: !result.ok
	};
};

export const actions: Actions = {
	create: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		const code = String(form.get('code') ?? '').trim();
		if (!name || !code) return fail(400, { error: 'Name and reference code are required.' });
		const response = await targetMutation(fetch, locals.accessToken, params.uid, '/forms', {
			method: 'POST',
			body: JSON.stringify({
				name,
				code,
				entity_type: String(form.get('entity_type') ?? 'applicant'),
				document_type: String(form.get('document_type') ?? '') || null,
				description: String(form.get('description') ?? '') || null
			})
		});
		if (!response.ok) return mutationFailure(response, 'Could not create the form.');
		return { ok: true, uid: ((await response.json()) as ApiForm).uid };
	},
	archive: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const uid = String((await request.formData()).get('uid') ?? '');
		const response = await targetMutation(fetch, locals.accessToken, params.uid, `/forms/${uid}`, {
			method: 'DELETE'
		});
		if (!response.ok) return mutationFailure(response, 'Could not archive the form.');
		return { ok: true };
	}
};
