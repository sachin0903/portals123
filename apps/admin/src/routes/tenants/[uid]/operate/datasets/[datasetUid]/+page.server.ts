import { fail, redirect } from '@sveltejs/kit';
import {
	parseInlineTable,
	toDataset,
	toDatasetVersion,
	type ApiDatasetDetail,
	type ApiDatasetVersion
} from '@graphene/ui/dataset-authoring';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';
export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [d, v, s] = await Promise.all([
		targetJson<ApiDatasetDetail & { used_by_templates?: number }>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/datasets/${params.datasetUid}`
		),
		targetJson<ApiDatasetVersion[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/datasets/${params.datasetUid}/versions`
		),
		targetJson<{ contact_id: number; name: string }[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/datasets/subjects'
		)
	]);
	return {
		tenantUid: tenant.uid,
		dataset: d.ok ? toDataset(d.data) : null,
		sample: d.ok ? d.data.sample : null,
		columns: d.ok ? d.data.columns : [],
		versions: v.ok ? v.data.map(toDatasetVersion) : [],
		subjects: s.ok ? s.data : [],
		usedByTemplates: d.ok ? (d.data.used_by_templates ?? 0) : 0,
		canManage: writing,
		loadError: !d.ok || !v.ok
	};
};
type ActionEvent = Parameters<NonNullable<Actions['updateDetails']>>[0];
type DatasetRun = {
	columns?: string[];
	rows?: unknown[][];
	truncated?: boolean;
	row_limit?: number;
	notice?: string | null;
};

async function json(e: ActionEvent, path: string, method: string, body?: Record<string, unknown>) {
	if (!canOperateWrite(e.locals)) return readOnlyFailure();
	const response = await targetMutation(
		e.fetch,
		e.locals.accessToken,
		e.params.uid,
		`/datasets/${e.params.datasetUid}${path}`,
		{ method, body: body === undefined ? undefined : JSON.stringify(body) }
	);
	if (!response.ok) return mutationFailure(response, 'Could not update dataset.');
	return { ok: true };
}
export const actions: Actions = {
	updateDetails: async (e) => {
		const f = await e.request.formData();
		return json(e, '', 'PATCH', {
			name: String(f.get('name') ?? ''),
			description: String(f.get('description') ?? '') || null,
			enabled: f.get('enabled') === 'on'
		});
	},
	addVersion: async (e) => {
		const f = await e.request.formData(),
			format = String(f.get('format') ?? '');
		if (format === 'sql')
			return json(e, '/versions', 'POST', { sql_query: String(f.get('sql_query') ?? '') });
		const p = parseInlineTable(String(f.get('columns') ?? ''), String(f.get('rows') ?? ''));
		if (p.raggedRows.length)
			return fail(400, { error: 'Some rows do not match the column count.' });
		return json(e, '/versions', 'POST', { inline: { columns: p.columns, rows: p.rows } });
	},
	runQuery: async (e) => {
		const f = await e.request.formData(),
			contact = String(f.get('contact_id') ?? '');
		const r = await targetJson<DatasetRun>(
			e.fetch,
			e.locals.accessToken,
			e.params.uid,
			`/datasets/${e.params.datasetUid}/data`,
			contact ? { contact_id: contact } : {}
		);
		if (!r.ok) return fail(r.status, { runError: 'The query did not run.' });
		return {
			run: {
				columns: r.data.columns ?? [],
				rows: r.data.rows ?? [],
				truncated: r.data.truncated ?? false,
				rowLimit: r.data.row_limit ?? 0,
				notice: r.data.notice ?? null
			}
		};
	},
	delete: async (e) => {
		const out = await json(e, '', 'DELETE', undefined);
		if ('status' in out) return out;
		redirect(303, `/tenants/${e.params.uid}/operate/datasets`);
	}
};
