import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { AppExecution, JourneyStaff } from '@graphene/ui/book/journeys';
import type { TaskRow } from '@graphene/ui/book/tasks';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'cases:read')) {
		error(403, 'You do not have permission to view applications.');
	}
	const canTasks = can(locals.permissions, locals.role, 'tasks:read');
	const canManage = can(locals.permissions, locals.role, 'cases:write');
	const canInvitePortal =
		can(locals.permissions, locals.role, 'contacts:write') &&
		can(locals.permissions, locals.role, 'members:write');
	return {
		canManage,
		canInvitePortal,
		record: loadRecord(locals.accessToken, fetch, params.uid, canTasks)
	};
};

async function loadRecord(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string,
	canTasks: boolean
) {
	const [res, tasks, staff] = await Promise.all([
		fetchJson<AppExecution>(fetch, `${apiUrl()}/applications/${uid}`, authHeaders(accessToken)),
		canTasks
			? fetchJson<TaskRow[]>(
					fetch,
					`${apiUrl()}/tasks?subject_type=execution&subject_id=${uid}`,
					authHeaders(accessToken)
				)
			: Promise.resolve(null),
		fetchJson<JourneyStaff[]>(fetch, `${apiUrl()}/applications/assignees`, authHeaders(accessToken))
	]);
	if (!res.ok) {
		if (res.status === 404) error(404, 'Application not found');
		error(res.status, 'Could not load the application.');
	}
	return {
		execution: res.data,
		tasks: tasks?.ok ? tasks.data : [],
		staff: staff.ok ? staff.data : []
	};
}

export const actions: Actions = {
	/** Staff action a manual step — a review/AML/identity outcome (with a note), a task, or opening
	 *  the account. */
	action: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const stepKey = String(form.get('step_key') ?? '').trim();
		const outcome = String(form.get('outcome') ?? 'passed').trim();
		const note = String(form.get('note') ?? '').trim();
		const adviserConfirmed = form.get('adviser_confirmed') === 'true';
		const pid = String(form.get('participant_contact_id') ?? '').trim();
		if (!caseUid || !stepKey) return fail(400, { error: 'No step specified.' });
		const res = await fetch(`${apiUrl()}/applications/${params.uid}/action`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				case_uid: caseUid,
				step_key: stepKey,
				outcome,
				note: note || null,
				adviser_confirmed: adviserConfirmed,
				participant_contact_id: pid ? Number(pid) : null
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Release a setup plan the journey is waiting on. The decision endpoint checks the
	 *  approver role; this does not re-run the hand-off. */
	approveRun: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const runUid = String(form.get('run_uid') ?? '').trim();
		if (!runUid) return fail(400, { error: 'No action specified.' });
		const res = await fetch(`${apiUrl()}/actions/runs/${encodeURIComponent(runUid)}/decision`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ decision: 'approve' })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Record a terms acceptance the client made in the adviser's presence. Not a way to skip the
	 *  step: the API files the acknowledgement for real, stamps who attested to it, and requires
	 *  the SIGNED COPY — a scan or photograph — which it files on the client's record and points
	 *  the acceptance at. Multipart, because of the file. */
	acknowledge: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const stepKey = String(form.get('step_key') ?? '').trim();
		const note = String(form.get('note') ?? '').trim();
		const pid = String(form.get('participant_contact_id') ?? '').trim();
		const file = form.get('file');
		if (!caseUid || !stepKey) return fail(400, { error: 'No step specified.' });
		if (!(file instanceof File) || file.size === 0) {
			return fail(400, { error: 'Upload the signed copy of the terms to record the acceptance.' });
		}
		const outbound = new FormData();
		outbound.set('case_uid', caseUid);
		outbound.set('step_key', stepKey);
		outbound.set('attested', 'true');
		if (note) outbound.set('note', note);
		if (pid) outbound.set('participant_contact_id', pid);
		outbound.set('file', file);
		const res = await fetch(`${apiUrl()}/applications/${params.uid}/acknowledge`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body: outbound
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Re-run a failed step's kick-off (a render that errored) instead of leaving the case pinned. */
	retryStep: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const stepKey = String(form.get('step_key') ?? '').trim();
		const pid = String(form.get('participant_contact_id') ?? '').trim();
		if (!caseUid || !stepKey) return fail(400, { error: 'No step specified.' });
		const res = await fetch(`${apiUrl()}/applications/${params.uid}/retry`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				case_uid: caseUid,
				step_key: stepKey,
				participant_contact_id: pid ? Number(pid) : null
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	identityManual: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const stepKey = String(form.get('step_key') ?? '').trim();
		const participant = String(form.get('participant_contact_id') ?? '').trim();
		if (!caseUid || !stepKey || !participant) {
			return fail(400, { error: 'No identity step specified.' });
		}
		const res = await fetch(`${apiUrl()}/applications/${params.uid}/identity-manual`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				case_uid: caseUid,
				step_key: stepKey,
				participant_contact_id: Number(participant)
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	identityDocument: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const itemUid = String(form.get('item_uid') ?? '').trim();
		const caseUid = String(form.get('case_uid') ?? '').trim();
		const stepKey = String(form.get('step_key') ?? '').trim();
		const participantContactId = String(form.get('participant_contact_id') ?? '').trim();
		const file = form.get('file');
		if (!itemUid || !caseUid || !stepKey || !participantContactId || !(file instanceof File)) {
			return fail(400, { error: 'Choose an identity document to upload.' });
		}
		const outbound = new FormData();
		outbound.set('case_uid', caseUid);
		outbound.set('step_key', stepKey);
		outbound.set('participant_contact_id', participantContactId);
		outbound.set('file', file);
		const res = await fetch(
			`${apiUrl()}/applications/${params.uid}/identity-documents/${itemUid}/fulfill`,
			{
				method: 'POST',
				headers: authHeaders(locals.accessToken),
				body: outbound
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Abandon a journey started in error. This is also what frees the client to be re-onboarded —
	 *  one live journey per flow per client is enforced in the database. */
	cancel: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const reason = String(form.get('reason') ?? '').trim();
		const res = await fetch(`${apiUrl()}/applications/${params.uid}/cancel`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ reason: reason || null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Give a profile's client a portal login in one step (create/reuse + link + invite email). */
	invitePortal: async ({ request, locals, fetch }) => {
		if (
			!can(locals.permissions, locals.role, 'contacts:write') ||
			!can(locals.permissions, locals.role, 'members:write')
		) {
			return fail(403, { error: 'You do not have permission to invite this client.' });
		}
		const form = await request.formData();
		const contactUid = String(form.get('contact_uid') ?? '').trim();
		if (!contactUid) return fail(400, { error: 'No client specified.' });
		const res = await fetch(`${apiUrl()}/firm/contacts/${contactUid}/portal-invite`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Who owns the platform-side work on this journey. Any staff member, not only an adviser. */
	assign: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'cases:write')) {
			return fail(403, { error: 'You do not have permission to manage applications.' });
		}
		const form = await request.formData();
		const userId = String(form.get('user_id') ?? '').trim();
		const res = await fetch(`${apiUrl()}/applications/${params.uid}/assignee`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ user_id: userId && userId !== 'none' ? userId : null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Settle a task raised against this journey (RLS decides who may). */
	completeTask: async ({ request, locals, fetch }) => {
		const form = await request.formData();
		const uid = String(form.get('task_uid') ?? '');
		if (!uid) return fail(400, { error: 'No task specified.' });
		const res = await fetch(`${apiUrl()}/tasks/${uid}/complete`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
