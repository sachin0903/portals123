import { fail, redirect } from '@sveltejs/kit';
import {
	toTemplate,
	toVersion,
	type ApiTemplate,
	type ApiVersion
} from '@graphene/ui/template-authoring';
import { toDataset, type ApiDataset } from '@graphene/ui/dataset-authoring';
import type { Dataset, Form } from '@graphene/ui';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type ApiForm = {
	uid: string;
	code: string;
	name: string;
	description: string | null;
	entity_type: string;
	status: string;
	field_count?: number;
	created_at: string;
	created_by: string | null;
};

const toForm = (form: ApiForm): Form => ({
	uid: form.uid,
	code: form.code,
	name: form.name,
	description: form.description ?? undefined,
	entityType: form.entity_type as Form['entityType'],
	status: form.status as Form['status'],
	fieldCount: form.field_count ?? 0,
	createdAt: form.created_at,
	createdBy: form.created_by ?? ''
});

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [t, v, forms, datasets] = await Promise.all([
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
		targetJson<ApiForm[]>(fetch, locals.accessToken, tenant.uid, '/forms'),
		targetJson<ApiDataset[]>(fetch, locals.accessToken, tenant.uid, '/datasets')
	]);
	return {
		tenantUid: tenant.uid,
		template: t.ok ? toTemplate(t.data) : null,
		versions: v.ok ? v.data.map(toVersion) : [],
		forms: forms.ok ? forms.data.map(toForm) : ([] as Form[]),
		datasets: datasets.ok ? datasets.data.map(toDataset) : ([] as Dataset[]),
		canManage: writing,
		loadError: !t.ok || !v.ok || !forms.ok || !datasets.ok
	};
};

type ActionEvent = Parameters<NonNullable<Actions['publish']>>[0];

async function patchTemplate(e: ActionEvent, payload: Record<string, unknown>) {
	if (!canOperateWrite(e.locals)) return readOnlyFailure();
	const response = await targetMutation(
		e.fetch,
		e.locals.accessToken,
		e.params.uid,
		`/templates/${e.params.templateUid}`,
		{ method: 'PATCH', body: JSON.stringify(payload) }
	);
	if (!response.ok) return mutationFailure(response, 'Could not update the template.');
	return { ok: true };
}

async function post(e: ActionEvent, path: string, method = 'POST') {
	if (!canOperateWrite(e.locals)) return readOnlyFailure();
	const response = await targetMutation(
		e.fetch,
		e.locals.accessToken,
		e.params.uid,
		`/templates/${e.params.templateUid}${path}`,
		{ method }
	);
	if (!response.ok) return mutationFailure(response, 'Could not update the template.');
	return { ok: true };
}
export const actions: Actions = {
	updateDetails: async (e) => {
		const form = await e.request.formData();
		const payload: Record<string, unknown> = {
			description: String(form.get('description') ?? '')
		};
		const name = String(form.get('name') ?? '').trim();
		const documentType = String(form.get('document_type') ?? '').trim();
		const renderer = String(form.get('renderer') ?? '').trim();
		if (name) payload.name = name;
		if (documentType) payload.document_type = documentType;
		if (renderer) payload.renderer = renderer;
		return patchTemplate(e, payload);
	},
	setDatasets: async (e) => {
		let codes: string[];
		try {
			codes = JSON.parse(String((await e.request.formData()).get('codes') ?? '[]'));
		} catch {
			return fail(400, { error: 'Malformed dataset selection.' });
		}
		return patchTemplate(e, { linked_datasets: codes });
	},
	setInputForm: async (e) => {
		const code = String((await e.request.formData()).get('code') ?? '').trim();
		return patchTemplate(e, { input_form_code: code || null });
	},
	publish: async (e) => {
		if (!canOperateWrite(e.locals)) return readOnlyFailure();
		const vid = String((await e.request.formData()).get('vid') ?? '');
		const response = await targetMutation(
			e.fetch,
			e.locals.accessToken,
			e.params.uid,
			`/templates/${e.params.templateUid}/versions/${vid}/publish`,
			{ method: 'POST' }
		);
		if (!response.ok) return mutationFailure(response, 'Could not publish.');
		return { ok: true };
	},
	archive: (e) => post(e, '', 'DELETE'),
	unarchive: (e) => post(e, '/unarchive'),
	duplicate: async (e) => {
		if (!canOperateWrite(e.locals)) return readOnlyFailure();
		const response = await targetMutation(
			e.fetch,
			e.locals.accessToken,
			e.params.uid,
			`/templates/${e.params.templateUid}/duplicate`,
			{ method: 'POST' }
		);
		if (!response.ok) return mutationFailure(response, 'Could not duplicate.');
		return { ok: true, uid: ((await response.json()) as ApiTemplate).uid };
	}
};
