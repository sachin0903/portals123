import { error, fail, redirect } from '@sveltejs/kit';
import type { AppExecution, JourneyStaff } from '@graphene/ui/book/journeys';
import type { TaskRow } from '@graphene/ui/book/tasks';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [execution, tasks, staff] = await Promise.all([
		targetJson<AppExecution>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/applications/${params.journeyUid}`
		),
		targetJson<TaskRow[]>(fetch, locals.accessToken, tenant.uid, '/tasks', {
			subject_type: 'execution',
			subject_id: params.journeyUid
		}),
		targetJson<JourneyStaff[]>(fetch, locals.accessToken, tenant.uid, '/applications/assignees')
	]);
	if (!execution.ok) {
		if (execution.status === 404) error(404, 'Journey not found.');
		error(execution.status || 500, 'Could not load the journey.');
	}
	return {
		execution: execution.data,
		tasks: tasks.ok ? tasks.data : [],
		tasksFailed: !tasks.ok,
		staff: staff.ok ? staff.data : [],
		writing
	};
};

async function mutate(
	event: Parameters<NonNullable<Actions[keyof Actions]>>[0],
	path: string,
	body: unknown
) {
	if (!canOperateWrite(event.locals)) return readOnlyFailure();
	// A FormData body goes through as multipart (`targetMutation` picks the headers by body type);
	// anything else is JSON.
	const response = await targetMutation(
		event.fetch,
		event.locals.accessToken,
		event.params.uid,
		path,
		{
			method: 'POST',
			body: body instanceof FormData ? body : JSON.stringify(body)
		}
	);
	if (!response.ok) return fail(response.status, { error: await detail(response) });
	return { ok: true };
}

export const actions: Actions = {
	action: async (event) => {
		const form = await event.request.formData();
		return mutate(event, `/applications/${event.params.journeyUid}/action`, {
			case_uid: String(form.get('case_uid') ?? ''),
			step_key: String(form.get('step_key') ?? ''),
			outcome: String(form.get('outcome') ?? 'passed'),
			note: String(form.get('note') ?? '').trim() || null,
			participant_contact_id: form.get('participant_contact_id')
				? Number(form.get('participant_contact_id'))
				: null
		});
	},
	/** Multipart: an in-person acceptance is recorded against the SIGNED COPY, which the API
	 *  requires and files on the client's record. */
	acknowledge: async (event) => {
		const form = await event.request.formData();
		const file = form.get('file');
		if (!(file instanceof File) || file.size === 0) {
			return fail(400, { error: 'Upload the signed copy of the terms to record the acceptance.' });
		}
		const outbound = new FormData();
		outbound.set('case_uid', String(form.get('case_uid') ?? ''));
		outbound.set('step_key', String(form.get('step_key') ?? ''));
		outbound.set('attested', 'true');
		const note = String(form.get('note') ?? '').trim();
		if (note) outbound.set('note', note);
		const pid = String(form.get('participant_contact_id') ?? '').trim();
		if (pid) outbound.set('participant_contact_id', pid);
		outbound.set('file', file);
		return mutate(event, `/applications/${event.params.journeyUid}/acknowledge`, outbound);
	},
	retryStep: async (event) => {
		const form = await event.request.formData();
		return mutate(event, `/applications/${event.params.journeyUid}/retry`, {
			case_uid: String(form.get('case_uid') ?? ''),
			step_key: String(form.get('step_key') ?? ''),
			participant_contact_id: form.get('participant_contact_id')
				? Number(form.get('participant_contact_id'))
				: null
		});
	},
	cancel: async (event) => {
		const form = await event.request.formData();
		return mutate(event, `/applications/${event.params.journeyUid}/cancel`, {
			reason: String(form.get('reason') ?? '').trim() || null
		});
	},
	assign: async (event) => {
		const form = await event.request.formData();
		const userId = String(form.get('user_id') ?? '').trim();
		return mutate(event, `/applications/${event.params.journeyUid}/assignee`, {
			user_id: userId && userId !== 'none' ? userId : null
		});
	},
	completeTask: async (event) => {
		const form = await event.request.formData();
		return mutate(event, `/tasks/${String(form.get('task_uid') ?? '')}/complete`, {});
	}
};
