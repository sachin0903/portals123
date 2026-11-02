import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

export const PUT: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user || !can(locals.permissions, locals.role, 'instructions:write'))
		return json({ error: 'You do not have permission to change the model.' }, { status: 403 });

	const response = await fetch(`${apiUrl()}/crm/accounts/${params.uid}/model`, {
		method: 'PUT',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(await request.json())
	});
	const body = await response.json().catch(() => ({}));
	return json(response.ok ? body : { error: body.detail ?? 'Could not change the model.' }, {
		status: response.status
	});
};
