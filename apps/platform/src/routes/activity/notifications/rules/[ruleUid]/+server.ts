import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Edit or delete one firm notification rule (proxies to /firm/notification-rules/{uid}; the API
 * enforces notifications:manage + firm_editable / firm-authored). */
export const PATCH: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/firm/notification-rules/${params.ruleUid}`, {
		method: 'PATCH',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(await request.json())
	});
	if (!res.ok) return json({ error: 'Could not save the rule.' }, { status: res.status });
	return json(await res.json());
};

export const DELETE: RequestHandler = async ({ locals, params, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const res = await fetch(`${apiUrl()}/firm/notification-rules/${params.ruleUid}`, {
		method: 'DELETE',
		headers: jsonHeaders(locals.accessToken)
	});
	return json({ ok: res.ok }, { status: res.ok ? 200 : res.status });
};
