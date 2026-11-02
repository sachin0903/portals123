import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import {
	toForm,
	toField,
	toResponse,
	type ApiForm,
	type ApiFormField,
	type ApiFormResponse,
	type FormResponseRow
} from '$lib/forms';
import type { Form, FormField } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const EMPTY = {
		form: null as Form | null,
		fields: [] as FormField[],
		responses: [] as FormResponseRow[],
		canManage: false,
		canSubmit: false,
		canReadResponses: false
	};
	if (!can(locals.permissions, locals.role, 'forms:read')) return EMPTY;
	// The response plane split out of the definition plane (the six-splits re-key): responses are
	// CLIENT data, so reading them takes forms:submit (submitting and seeing what came back travel
	// together) or documents:read (the client-paperwork readers) — never forms:read alone. The
	// fetch is skipped entirely for a definition-only viewer, mirroring the API's own gate.
	const canSubmit = can(locals.permissions, locals.role, 'forms:submit');
	const canReadResponses = canSubmit || can(locals.permissions, locals.role, 'documents:read');
	const headers = authHeaders(locals.accessToken);
	const [formRes, fieldsRes, responsesRes] = await Promise.all([
		fetch(`${apiUrl()}/forms/${params.uid}`, { headers }),
		fetch(`${apiUrl()}/forms/${params.uid}/fields`, { headers }),
		canReadResponses ? fetch(`${apiUrl()}/forms/${params.uid}/responses`, { headers }) : null
	]);
	if (!formRes.ok) return EMPTY;
	const form: ApiForm = await formRes.json();
	const fields: ApiFormField[] = fieldsRes.ok ? await fieldsRes.json() : [];
	const responses: ApiFormResponse[] = responsesRes?.ok ? await responsesRes.json() : [];
	return {
		form: toForm(form),
		fields: fields.map(toField),
		responses: responses.map(toResponse),
		canManage: can(locals.permissions, locals.role, 'forms:write'),
		canSubmit,
		canReadResponses
	};
};

export const actions: Actions = {
	/** Publish a draft form. */
	publish: async ({ params, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'forms:write')) {
			return fail(403, { error: 'You do not have permission to publish forms.' });
		}
		const res = await fetch(`${apiUrl()}/forms/${params.uid}/publish`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Archive a form (soft). Refused (409) by the API while an open request still points at it. */
	archive: async ({ params, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'forms:write')) {
			return fail(403, { error: 'You do not have permission to archive forms.' });
		}
		const res = await fetch(`${apiUrl()}/forms/${params.uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Restore an archived form (comes back as a draft). */
	unarchive: async ({ params, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'forms:write')) {
			return fail(403, { error: 'You do not have permission to restore forms.' });
		}
		const res = await fetch(`${apiUrl()}/forms/${params.uid}/unarchive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Record a standalone response from the preview tab.
	 *
	 * forms:submit, not forms:write (the six-splits re-key): recording a response is the response
	 * plane, not form authoring — the API's endpoint demands the same slug. */
	submitResponse: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'forms:submit')) {
			return fail(403, { error: 'You do not have permission to submit responses.' });
		}
		let answers: unknown;
		try {
			answers = JSON.parse(String((await request.formData()).get('answers') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed response.' });
		}
		const res = await fetch(`${apiUrl()}/forms/${params.uid}/responses`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ answers })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
