import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/** File a GIA or ISA illustration. The API calculates it and queues the PDF. */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'illustrations:write')) {
		return json(
			{ error: 'You do not have permission to generate illustrations.' },
			{ status: 403 }
		);
	}
	const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/illustrations/investment`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: await request.text()
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
