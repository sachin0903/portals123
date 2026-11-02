import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Edit or delete one notification rule. Tenant id resolved from the uid; API enforces
 * `tenants:write` (and, on the firm surface, `firm_editable`). */
export const PATCH: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/notification-rules/${params.ruleUid}`,
		{
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(await request.json())
		}
	);
	if (!res.ok) return json({ error: 'Could not save the rule.' }, { status: res.status });
	return json(await res.json());
};

export const DELETE: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(
		`${apiUrl()}/admin/tenants/${params.uid}/notification-rules/${params.ruleUid}`,
		{ method: 'DELETE', headers: jsonHeaders(locals.accessToken) }
	);
	return json({ ok: res.ok }, { status: res.ok ? 200 : res.status });
};
