import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { PageServerLoad } from './$types';

export type ReportSummary = {
	code: string;
	name: string;
	description: string;
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'reports:read')) error(403, 'Not authorised');
	const res = await fetchJson<ReportSummary[]>(
		fetch,
		`${apiUrl()}/reports`,
		authHeaders(locals.accessToken)
	);
	return {
		reports: res.ok ? res.data : [],
		loadError: !res.ok,
		canExport: can(locals.permissions, locals.role, 'reports:export')
	};
};
