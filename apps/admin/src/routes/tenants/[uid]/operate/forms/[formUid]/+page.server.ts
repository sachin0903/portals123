import { fail, redirect } from '@sveltejs/kit';
import type { Form, FormField } from '@graphene/ui';
import {
	toField,
	toForm,
	toResponse,
	type ApiForm,
	type ApiFormField,
	type ApiFormResponse,
	type FormResponseRow
} from '@graphene/ui/form-builder';
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
	const [form, fields, responses] = await Promise.all([
		targetJson<ApiForm>(fetch, locals.accessToken, tenant.uid, `/forms/${params.formUid}`),
		targetJson<ApiFormField[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/forms/${params.formUid}/fields`
		),
		targetJson<ApiFormResponse[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/forms/${params.formUid}/responses`
		)
	]);
	return {
		form: form.ok ? toForm(form.data) : (null as Form | null),
		fields: fields.ok ? fields.data.map(toField) : ([] as FormField[]),
		responses: responses.ok ? responses.data.map(toResponse) : ([] as FormResponseRow[]),
		canManage: writing,
		canSubmit: writing,
		canReadResponses: responses.ok
	};
};

async function verb(
	event: Parameters<NonNullable<Actions[keyof Actions]>>[0],
	path: string,
	method: 'POST' | 'DELETE'
) {
	if (!canOperateWrite(event.locals)) return readOnlyFailure();
	const response = await targetMutation(
		event.fetch,
		event.locals.accessToken,
		event.params.uid,
		path,
		{
			method
		}
	);
	if (!response.ok) return mutationFailure(response, 'Could not update the form.');
	return { ok: true };
}

export const actions: Actions = {
	publish: (event) => verb(event, `/forms/${event.params.formUid}/publish`, 'POST'),
	archive: (event) => verb(event, `/forms/${event.params.formUid}`, 'DELETE'),
	unarchive: (event) => verb(event, `/forms/${event.params.formUid}/unarchive`, 'POST'),
	submitResponse: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		let answers: unknown;
		try {
			answers = JSON.parse(String((await event.request.formData()).get('answers') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed response.' });
		}
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/forms/${event.params.formUid}/responses`,
			{ method: 'POST', body: JSON.stringify({ answers }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not record the response.');
		return { ok: true };
	}
};
