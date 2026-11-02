import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

export type ReportRow = {
	code: string;
	name: string;
	description: string;
	enabled: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'mi_reports:read')) error(403, 'Not authorised');
	const res = await fetch(`${apiUrl()}/operations/mi-reports`, {
		headers: authHeaders(locals.accessToken)
	});
	const reports: ReportRow[] = res.ok ? ((await res.json()) as ReportRow[]) : [];
	return {
		reports,
		canManage: can(locals.permissions, locals.role, 'mi_reports:write'),
		loadError: !res.ok
	};
};

export const actions: Actions = {
	toggle: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'mi_reports:write')) {
			return fail(403, { error: 'You do not have permission to change reports.' });
		}
		const form = await request.formData();
		const code = String(form.get('code') ?? '');
		const enabled = String(form.get('enabled') ?? '') === 'true';
		const res = await fetch(`${apiUrl()}/operations/mi-reports/${encodeURIComponent(code)}`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ enabled })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
