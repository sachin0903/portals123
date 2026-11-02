import { fail, redirect } from '@sveltejs/kit';
import type { Template, Form, Dataset } from '@graphene/ui';
import { toTemplate, type ApiTemplate } from '@graphene/ui/template-authoring';
import { toDataset, type ApiDataset } from '@graphene/ui/dataset-authoring';
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

const toForm = (f: ApiForm): Form => ({
	uid: f.uid,
	code: f.code,
	name: f.name,
	description: f.description ?? undefined,
	entityType: f.entity_type as Form['entityType'],
	status: f.status as Form['status'],
	fieldCount: f.field_count ?? 0,
	createdAt: f.created_at,
	createdBy: f.created_by ?? ''
});
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [live, archived, forms, datasets] = await Promise.all([
		targetJson<ApiTemplate[]>(fetch, locals.accessToken, tenant.uid, '/templates'),
		targetJson<ApiTemplate[]>(fetch, locals.accessToken, tenant.uid, '/templates', {
			status: 'archived'
		}),
		targetJson<ApiForm[]>(fetch, locals.accessToken, tenant.uid, '/forms'),
		targetJson<ApiDataset[]>(fetch, locals.accessToken, tenant.uid, '/datasets')
	]);
	return {
		tenantUid: tenant.uid,
		templates: live.ok ? live.data.map(toTemplate) : ([] as Template[]),
		archivedTemplates: archived.ok ? archived.data.map(toTemplate) : [],
		forms: forms.ok ? forms.data.map(toForm) : ([] as Form[]),
		datasets: datasets.ok ? datasets.data.map(toDataset) : ([] as Dataset[]),
		canManage: writing,
		loadError: ![live, archived, forms, datasets].every((x) => x.ok)
	};
};
export const actions: Actions = {
	create: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		let input: Record<string, unknown>;
		try {
			input = JSON.parse(String((await request.formData()).get('input') ?? ''));
		} catch {
			return fail(400, { error: 'Malformed template.' });
		}
		const response = await targetMutation(fetch, locals.accessToken, params.uid, '/templates', {
			method: 'POST',
			body: JSON.stringify({
				code: String(input.code ?? ''),
				name: String(input.name ?? ''),
				document_type: String(input.documentType ?? ''),
				renderer: input.renderer ?? 'weasy',
				description: input.description ?? null,
				linked_datasets: Array.isArray(input.linkedDatasets) ? input.linkedDatasets : [],
				input_form_code: input.inputFormCode ?? null
			})
		});
		if (!response.ok) return mutationFailure(response, 'Could not create the template.');
		return { ok: true, uid: ((await response.json()) as ApiTemplate).uid };
	},
	archive: async (e) => lifecycle(e, 'DELETE', ''),
	unarchive: async (e) => lifecycle(e, 'POST', '/unarchive'),
	duplicate: async (e) => lifecycle(e, 'POST', '/duplicate', true)
};
type ActionEvent = Parameters<NonNullable<Actions['create']>>[0];

async function lifecycle(
	{ request, locals, fetch, params }: ActionEvent,
	method: string,
	suffix: string,
	returns = false
) {
	if (!canOperateWrite(locals)) return readOnlyFailure();
	const uid = String((await request.formData()).get('uid') ?? '');
	if (!uid) return fail(400, { error: 'No template specified.' });
	const response = await targetMutation(
		fetch,
		locals.accessToken,
		params.uid,
		`/templates/${uid}${suffix}`,
		{ method }
	);
	if (!response.ok) return mutationFailure(response, 'Could not update the template.');
	return returns ? { ok: true, uid: ((await response.json()) as ApiTemplate).uid } : { ok: true };
}
