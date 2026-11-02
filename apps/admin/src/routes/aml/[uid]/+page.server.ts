import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { clientHref } from '$lib/aml';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

/**
 * One check's old address. The console is worked by client now, so a check link — a task, a
 * bookmark, the Compliance tab before it moved — opens its client's page on that check.
 */
export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:read')) error(403, 'Not authorised');

	let res: Response;
	try {
		res = await fetch(`${apiUrl()}/aml/checks/${encodeURIComponent(params.uid)}/client`, {
			headers: authHeaders(locals.accessToken)
		});
	} catch {
		error(503, 'The service is unavailable.');
	}
	if (res.status === 404) error(404, 'Check not found.');
	if (!res.ok) error(res.status, await detail(res));
	const found: { key: string; check_uid: string } = await res.json();
	redirect(303, clientHref(found.key, found.check_uid));
};
