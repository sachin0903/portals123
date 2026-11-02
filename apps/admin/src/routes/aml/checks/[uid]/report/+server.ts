import { error, redirect } from '@sveltejs/kit';
import { can } from '$lib/permissions';
import { relayReport } from '$lib/server/aml';
import type { RequestHandler } from './$types';

// One check's report (identity or AML) as a PDF download, through a same-origin relay:
// browser -> this route -> API `/aml/checks/{uid}/report`, which makes it.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:read')) error(403, 'Not authorised');
	return relayReport(
		fetch,
		locals.accessToken,
		`/aml/checks/${encodeURIComponent(params.uid)}/report`
	);
};
