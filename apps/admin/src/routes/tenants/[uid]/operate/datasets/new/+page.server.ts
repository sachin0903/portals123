import { fail, redirect } from '@sveltejs/kit';
import { parseInlineTable } from '@graphene/ui/dataset-authoring';
import { canOperateWrite, mutationFailure, readOnlyFailure, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';
export const load: PageServerLoad = async ({ locals, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	return { tenantUid: tenant.uid, canManage: writing, canFirmWideSql: true };
};
export const actions: Actions = {
	default: async ({ locals, fetch, params, request }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const f = await request.formData(),
			format = String(f.get('format') ?? 'inline');
		const body: Record<string, unknown> = {
			code: String(f.get('code') ?? '').trim(),
			name: String(f.get('name') ?? '').trim(),
			description: String(f.get('description') ?? '').trim() || null,
			format,
			scope: String(f.get('scope') ?? 'global')
		};
		if (format === 'sql') body.sql_query = String(f.get('sql_query') ?? '');
		else {
			const parsed = parseInlineTable(String(f.get('columns') ?? ''), String(f.get('rows') ?? ''));
			if (parsed.raggedRows.length)
				return fail(400, { error: 'Some rows do not match the column count.' });
			body.inline = { columns: parsed.columns, rows: parsed.rows };
		}
		const response = await targetMutation(fetch, locals.accessToken, params.uid, '/datasets', {
			method: 'POST',
			body: JSON.stringify(body)
		});
		if (!response.ok) return mutationFailure(response, 'Could not create dataset.');
		const ds = (await response.json()) as { uid: string };
		redirect(303, `/tenants/${params.uid}/operate/datasets/${ds.uid}`);
	}
};
