import { error, redirect } from '@sveltejs/kit';
import { can } from '$lib/permissions';
import { targetRequest } from '$lib/operate';
import { detail } from '$lib/api';
import type { RequestHandler } from './$types';

// The same audit PDF the firm's own journey page downloads, fetched in the target firm's context.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'tenant_portal:read')) {
		error(403, 'You do not have permission to view this firm.');
	}
	const res = await targetRequest(
		fetch,
		locals.accessToken,
		params.uid,
		`/applications/${params.journeyUid}/application.pdf`
	);
	if (!res.ok) error(res.status, await detail(res));
	const headers = new Headers({
		'content-type': 'application/pdf',
		'cache-control': 'private, no-store'
	});
	const disposition = res.headers.get('content-disposition');
	if (disposition) headers.set('content-disposition', disposition);
	return new Response(await res.arrayBuffer(), { headers });
};
