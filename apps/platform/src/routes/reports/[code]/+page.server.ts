import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { PageServerLoad } from './$types';

export type ReportParam = {
	key: string;
	label: string;
	kind: string;
	required: boolean;
	options: string[];
};

export type ReportColumn = { key: string; label: string; kind: string };

export type ReportDetail = {
	code: string;
	name: string;
	description: string;
	columns: ReportColumn[];
	params: ReportParam[];
};

export type Preview = {
	rows: Record<string, string | number | null>[];
	truncated: boolean;
	narrowed: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch, params, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'reports:read')) error(403, 'Not authorised');

	const reportRes = await fetch(`${apiUrl()}/reports/${encodeURIComponent(params.code)}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (reportRes.status === 404) error(404, 'That report is not available.');
	if (!reportRes.ok) error(reportRes.status, await detail(reportRes));
	const report = (await reportRes.json()) as ReportDetail;

	const values: Record<string, string> = {};
	for (const param of report.params) {
		values[param.key] = url.searchParams.get(param.key) ?? '';
	}
	const missing = report.params.some((param) => param.required && !values[param.key]);

	let preview: Preview | null = null;
	let previewError: string | null = null;
	if (!missing) {
		const qs = new URLSearchParams();
		for (const [key, value] of Object.entries(values)) {
			if (value) qs.set(key, value);
		}
		const previewRes = await fetch(
			`${apiUrl()}/reports/${encodeURIComponent(params.code)}/preview?${qs}`,
			{ headers: authHeaders(locals.accessToken) }
		);
		if (previewRes.ok) preview = (await previewRes.json()) as Preview;
		else previewError = await detail(previewRes);
	}

	return {
		report,
		crumb: report.name,
		values,
		missing,
		preview,
		previewError,
		canExport: can(locals.permissions, locals.role, 'reports:export')
	};
};
