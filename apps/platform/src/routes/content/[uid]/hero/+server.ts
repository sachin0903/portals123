import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request, locals, fetch, params }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'content:write')) error(403, 'Not permitted');
	const incoming = await request.formData().catch(() => null);
	const file = incoming?.get('file');
	if (!(file instanceof File)) error(400, 'No image was uploaded');
	const body = new FormData();
	body.append('file', file, file.name || 'hero');
	const res = await fetch(`${apiUrl()}/content/items/${params.uid}/hero`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken),
		body
	});
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
