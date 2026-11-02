import { error, fail, redirect } from '@sveltejs/kit';
import {
	apiUrl,
	authHeaders,
	jsonHeaders,
	detail,
	type IntegrationDetailResponse,
	type JobRunsPage
} from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

const RUNS_PAGE_SIZE = 20;

const writeRefusal = (
	locals: { permissions: string[]; role: string | null },
	action: string,
	subject = 'integrations'
) =>
	can(locals.permissions, locals.role, 'integrations:write')
		? null
		: fail(403, {
				action,
				error: `You need the integrations:write permission to manage ${subject}.`
			});

export const load: PageServerLoad = async ({ locals, fetch, params, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'integrations:read')) error(403, 'Not authorised');

	const headers = authHeaders(locals.accessToken);

	const detailRes = await fetch(`${apiUrl()}/integrations/${params.uid}`, { headers });
	if (detailRes.status === 404) error(404, 'Integration not found');
	if (!detailRes.ok) error(detailRes.status, await detail(detailRes));
	const body: IntegrationDetailResponse = await detailRes.json();

	// Job Runs tab — paged/searched via the URL, same as every other listing in the estate (see
	// `routes/aml/+page.server.ts`).
	const runsPage = Math.max(1, Number(url.searchParams.get('runsPage') ?? 1) || 1);
	const runsQ = url.searchParams.get('runsQ')?.trim() ?? '';
	const runsQuery = new URLSearchParams({ page: String(runsPage), limit: String(RUNS_PAGE_SIZE) });
	if (runsQ) runsQuery.set('search', runsQ);

	let runsPageData: JobRunsPage = { items: [], total: 0, page: runsPage, limit: RUNS_PAGE_SIZE };
	let runsError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/integrations/${params.uid}/runs?${runsQuery}`, {
			headers
		});
		if (res.ok) runsPageData = await res.json();
		else runsError = await detail(res);
	} catch {
		runsError = 'The service is unavailable.';
	}

	return {
		integration: body.integration,
		firms: body.firms,
		features: body.features,
		lastRun: body.last_run,
		runs: runsPageData.items,
		runsTotal: runsPageData.total,
		runsPage,
		runsQ,
		runsError,
		canWrite: can(locals.permissions, locals.role, 'integrations:write')
	};
};

export const actions: Actions = {
	/** Approve/reject/reconfigure this integration — the same rule set as the list's `?/update`. */
	updateIntegration: async ({ request, params, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const refused = writeRefusal(locals, 'updateIntegration');
		if (refused) return refused;

		const data = await request.formData();
		const status = String(data.get('status') ?? 'pending');
		const reason = String(data.get('reason') ?? '').trim();
		if (status === 'rejected' && !reason)
			return fail(400, {
				action: 'updateIntegration',
				error: 'A reason is required to reject an integration.'
			});

		const firmIdRaw = String(data.get('firm_id') ?? '').trim();
		const putBody = {
			firm_id: firmIdRaw ? Number(firmIdRaw) : null,
			status,
			enabled: data.get('enabled') === 'on',
			features: data.getAll('features').map(String),
			reason: reason || null
		};

		const res = await fetch(`${apiUrl()}/integrations/${params.uid}`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(putBody)
		});
		if (!res.ok) return fail(res.status, { action: 'updateIntegration', error: await detail(res) });
		return { action: 'updateIntegration', ok: true };
	}
};
