import { error, json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'platforms:write')) error(403, 'Not authorised');
	const provider = params.slug;
	const run = params.run;
	if (!provider || !run) error(404, 'Not a live platform');
	const res = await fetch(`${apiUrl()}/admin/platforms/${provider}/runs/${run}/cancel`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ detail: await detail(res) }, { status: res.status });
	return json(await res.json());
};
