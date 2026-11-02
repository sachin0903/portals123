import { error, fail, redirect } from '@sveltejs/kit';
import { parseStepAnswers } from '@graphene/ui/book/journeys';
import type { FormField } from '@graphene/ui';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type ApiField = {
	uid: string;
	field_key: string;
	field_type: string;
	label: string;
	section: string | null;
	description: string | null;
	required: boolean;
	conditions: FormField['conditions'];
	validation: FormField['validation'];
	options: FormField['options'];
	sort_order: number;
};
type StepForm = {
	title: string | null;
	name: string;
	description: string | null;
	status: string;
	fields: ApiField[];
	answers: Record<string, unknown> | null;
	context?: Record<string, unknown>;
};

const field = (value: ApiField): FormField => ({
	uid: value.uid,
	fieldKey: value.field_key,
	fieldType: value.field_type,
	label: value.label,
	section: value.section,
	description: value.description,
	required: value.required,
	conditions: value.conditions,
	validation: value.validation,
	options: value.options,
	sortOrder: value.sort_order
});

export const load: PageServerLoad = async ({ params, url, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const participant = url.searchParams.get('participant');
	const result = await targetJson<StepForm>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/applications/${params.journeyUid}/cases/${params.caseUid}/steps/${params.stepKey}/form`,
		participant ? { participant_contact_id: participant } : {}
	);
	if (!result.ok) {
		if (result.status === 404) error(404, 'That journey step could not be found.');
		if (result.status === 422) error(422, 'That journey step has no form to complete.');
		error(result.status || 500, 'Could not load the journey step.');
	}
	return {
		form: { ...result.data, fields: result.data.fields.map(field) },
		participant,
		journeyUid: params.journeyUid,
		caseUid: params.caseUid,
		stepKey: params.stepKey,
		writing
	};
};

export const actions: Actions = {
	submit: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const answers = parseStepAnswers(form.get('answers'));
		if (!answers.ok) return fail(422, { error: 'Your answers were not submitted.' });
		const participant = String(form.get('participant') ?? '').trim();
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/applications/${event.params.journeyUid}/cases/${event.params.caseUid}/steps/${event.params.stepKey}/form`,
			{
				method: 'POST',
				body: JSON.stringify({
					answers: answers.answers,
					participant_contact_id: participant ? Number(participant) : null
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		redirect(303, `/tenants/${event.params.uid}/operate/journeys/${event.params.journeyUid}`);
	}
};
