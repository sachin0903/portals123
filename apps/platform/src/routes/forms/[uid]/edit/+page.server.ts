import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { toForm, toField, type ApiForm, type ApiFormField } from '$lib/forms';
import type { Form, FormField } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'forms:read')) {
		return { form: null as Form | null, fields: [] as FormField[], canManage: false };
	}
	const headers = authHeaders(locals.accessToken);
	const [formRes, fieldsRes] = await Promise.all([
		fetch(`${apiUrl()}/forms/${params.uid}`, { headers }),
		fetch(`${apiUrl()}/forms/${params.uid}/fields`, { headers })
	]);
	if (!formRes.ok) return { form: null, fields: [], canManage: false };
	const form: ApiForm = await formRes.json();
	const fields: ApiFormField[] = fieldsRes.ok ? await fieldsRes.json() : [];
	return {
		form: toForm(form),
		fields: fields.map(toField),
		canManage: can(locals.permissions, locals.role, 'forms:write')
	};
};

export const actions: Actions = {
	/** Replace the whole field list (the builder's autosave). Body: `fields` = JSON FormFieldIn[]. */
	saveFields: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'forms:write')) {
			return fail(403, { error: 'You do not have permission to edit forms.' });
		}
		const data = await request.formData();
		let fields: unknown;
		let scoring: unknown;
		const scoringRaw = data.get('scoring');
		try {
			fields = JSON.parse(String(data.get('fields') ?? '[]'));
			if (scoringRaw != null) scoring = JSON.parse(String(scoringRaw));
		} catch {
			return fail(400, { error: 'Malformed fields.' });
		}
		const res = await fetch(`${apiUrl()}/forms/${params.uid}/fields`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ fields })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		if (scoringRaw != null) {
			const scored = await fetch(`${apiUrl()}/forms/${params.uid}`, {
				method: 'PATCH',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ scoring })
			});
			if (!scored.ok) return fail(scored.status, { error: await detail(scored) });
		}
		return { ok: true };
	}
};
