import { fail, redirect } from '@sveltejs/kit';
import type { Form, FormField } from '@graphene/ui';
import { toField, toForm, type ApiForm, type ApiFormField } from '@graphene/ui/form-builder';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [form, fields] = await Promise.all([
		targetJson<ApiForm>(fetch, locals.accessToken, tenant.uid, `/forms/${params.formUid}`),
		targetJson<ApiFormField[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/forms/${params.formUid}/fields`
		)
	]);
	return {
		form: form.ok ? toForm(form.data) : (null as Form | null),
		fields: fields.ok ? fields.data.map(toField) : ([] as FormField[]),
		canManage: writing
	};
};

export const actions: Actions = {
	saveFields: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const data = await event.request.formData();
		let fields: unknown;
		let scoring: unknown;
		const scoringRaw = data.get('scoring');
		try {
			fields = JSON.parse(String(data.get('fields') ?? '[]'));
			if (scoringRaw != null) scoring = JSON.parse(String(scoringRaw));
		} catch {
			return fail(400, { error: 'Malformed fields.' });
		}
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/forms/${event.params.formUid}/fields`,
			{ method: 'PUT', body: JSON.stringify({ fields }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not save the form.');
		if (scoringRaw != null) {
			const scored = await targetMutation(
				event.fetch,
				event.locals.accessToken,
				event.params.uid,
				`/forms/${event.params.formUid}`,
				{ method: 'PATCH', body: JSON.stringify({ scoring }) }
			);
			if (!scored.ok) return mutationFailure(scored, 'Could not save scoring.');
		}
		return { ok: true };
	}
};
