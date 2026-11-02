import { error, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

// The journey's audit PDF — questions, answers, the terms agreed, and the signature. The API
// renders it on request; this route only checks the same read the journey page does and streams
// the bytes so the browser saves `application_{uid}.pdf`.
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'cases:read')) {
		error(403, 'You do not have permission to view applications.');
	}
	const res = await fetch(`${apiUrl()}/applications/${params.uid}/application.pdf`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	const headers = new Headers({
		'content-type': 'application/pdf',
		'cache-control': 'private, no-store'
	});
	const disposition = res.headers.get('content-disposition');
	if (disposition) headers.set('content-disposition', disposition);
	return new Response(await res.arrayBuffer(), { headers });
};
