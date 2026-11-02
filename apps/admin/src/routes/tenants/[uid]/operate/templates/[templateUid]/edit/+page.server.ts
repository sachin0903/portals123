import { fail, redirect } from '@sveltejs/kit';
import {
	toTemplate,
	type ApiTemplate,
	type ApiVersion,
	type ApiVersionDetail
} from '@graphene/ui/template-authoring';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type ApiForm = { uid: string; code: string };
type ApiFormField = { field_key: string; label: string };
type DatasetOption = {
	uid: string;
	code: string;
	name: string;
	scope: string;
	columns?: string[];
	sample: { columns: string[]; rows: unknown[][] } | null;
};
type PreviewClient = { contact_id: number; name: string | null };

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [t, v, datasets, clients] = await Promise.all([
		targetJson<ApiTemplate>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/templates/${params.templateUid}`
		),
		targetJson<ApiVersion[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/templates/${params.templateUid}/versions`
		),
		targetJson<DatasetOption[]>(fetch, locals.accessToken, tenant.uid, '/datasets', {
			include: 'sample'
		}),
		targetJson<PreviewClient[]>(fetch, locals.accessToken, tenant.uid, '/documents/clients')
	]);
	let restore = null;
	if (v.ok && v.data[0]) {
		const d = await targetJson<ApiVersionDetail>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/templates/${params.templateUid}/versions/${v.data[0].uid}`
		);
		if (d.ok) restore = d.data.schema;
	}
	let formFields: { key: string; label: string }[] = [];
	if (t.ok && t.data.input_form_code) {
		const forms = await targetJson<ApiForm[]>(fetch, locals.accessToken, tenant.uid, '/forms');
		const linked = forms.ok
			? forms.data.find((form) => form.code === t.data.input_form_code)
			: undefined;
		if (linked) {
			const fields = await targetJson<ApiFormField[]>(
				fetch,
				locals.accessToken,
				tenant.uid,
				`/forms/${linked.uid}/fields`
			);
			if (fields.ok) {
				formFields = fields.data.map((field) => ({
					key: field.field_key,
					label: field.label
				}));
			}
		}
	}
	return {
		tenantUid: tenant.uid,
		template: t.ok ? toTemplate(t.data) : null,
		restore,
		formFields,
		versions: v.ok
			? v.data.map((x) => ({
					uid: x.uid,
					versionNumber: x.version_number,
					status: x.status,
					savedAt: x.published_at ?? x.created_at,
					author: x.published_by ?? x.created_by
				}))
			: [],
		datasetOptions: datasets.ok
			? datasets.data.map((dataset) => ({
					uid: dataset.uid,
					code: dataset.code,
					name: dataset.name,
					scope: dataset.scope,
					columns: dataset.columns ?? dataset.sample?.columns ?? [],
					sample: dataset.sample
				}))
			: [],
		previewClients: clients.ok
			? clients.data.map((client) => ({
					contactId: client.contact_id,
					name: client.name ?? `Client ${client.contact_id}`
				}))
			: [],
		brandLogo: null,
		canManage: writing,
		loadError: !t.ok || !v.ok
	};
};
export const actions: Actions = {
	saveVersion: async ({ locals, fetch, params, request }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		let schema;
		try {
			schema = JSON.parse(String((await request.formData()).get('schema') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed template content.' });
		}
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/templates/${params.templateUid}/versions`,
			{ method: 'POST', body: JSON.stringify({ schema }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not save template.');
		return { ok: true };
	}
};
