import { fail, redirect } from '@sveltejs/kit';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type Illustration = {
	uid: string;
	document_uid?: string | null;
	name: string | null;
	created_at: string;
	product_type?: string | null;
	status?: string | null;
	shared_with_client?: boolean;
	inputs: Record<string, unknown>;
};
type Model = { id: number; name: string; target_return: number | string | null };

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const [illustrations, models] = await Promise.all([
		targetJson<Illustration[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/crm/contacts/${params.clientUid}/illustrations`
		),
		targetJson<Model[]>(fetch, locals.accessToken, tenant.uid, '/crm/models')
	]);
	return {
		illustrations: illustrations.ok ? illustrations.data : [],
		models: models.ok ? models.data : [],
		loadError: !illustrations.ok
	};
};

export const actions: Actions = {
	generate: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const modelId = Number(form.get('model_id'));
		const fund = Number(form.get('existing_fund_value'));
		const exitAge = Number(form.get('exit_age'));
		if (!Number.isInteger(modelId) || !Number.isFinite(fund) || fund < 0)
			return fail(400, { error: 'Choose a model and enter a valid fund value.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/crm/contacts/${event.params.clientUid}/illustrations`,
			{
				method: 'POST',
				body: JSON.stringify({
					product_type: String(form.get('product_type') ?? 'SIPP'),
					existing_fund_value: fund,
					model_id: modelId,
					quotation_type: String(form.get('quotation_type') ?? 'Accumulation'),
					exit_age: Number.isInteger(exitAge) ? exitAge : null
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true, result: await response.json() };
	},
	share: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const uid = String((await event.request.formData()).get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No illustration specified.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/crm/contacts/${event.params.clientUid}/illustrations/${uid}/share`,
			{ method: 'POST' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	}
};
