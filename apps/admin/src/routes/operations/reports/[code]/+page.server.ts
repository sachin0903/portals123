import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

export type ReportDetail = {
	code: string;
	name: string;
	description: string;
	enabled: boolean;
	columns: { key: string; label: string; kind: string }[];
	params: { key: string; label: string; kind: string; required: boolean; options: string[] }[];
};

export const load: PageServerLoad = async ({ locals, fetch, params }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'mi_reports:read')) error(403, 'Not authorised');
	const res = await fetch(`${apiUrl()}/operations/mi-reports`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	const reports = (await res.json()) as ReportDetail[];
	const report = reports.find((item) => item.code === params.code);
	if (!report) error(404, 'Unknown report');
	return {
		report,
		crumb: report.name,
		canManage: can(locals.permissions, locals.role, 'mi_reports:write')
	};
};

export const actions: Actions = {
	toggle: async ({ request, locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'mi_reports:write')) {
			return fail(403, { error: 'You do not have permission to change reports.' });
		}
		const form = await request.formData();
		const enabled = String(form.get('enabled') ?? '') === 'true';
		const res = await fetch(
			`${apiUrl()}/operations/mi-reports/${encodeURIComponent(params.code)}`,
			{
				method: 'PUT',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ enabled })
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
