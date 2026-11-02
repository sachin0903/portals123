import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, detail, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Generate a pension illustration for this client.
 *
 * A thin pass-through: the API owns building the CTC1 XML from the client record, calling the
 * engine and filing the returned PDF. The browser never sees the engine, and the client's data
 * never round-trips through the form.
 */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	// illustrations:write (the six-splits re-key): generating calls a paid external engine, and
	// the API's create endpoint demands the same slug — this keeps the proxy from forwarding an
	// ungated call at all.
	if (!can(locals.permissions, locals.role, 'illustrations:write')) {
		return json(
			{ error: 'You do not have permission to generate illustrations.' },
			{ status: 403 }
		);
	}

	const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/illustrations`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: await request.text()
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	return json(await res.json());
};
