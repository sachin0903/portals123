import { error, json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

// Resolve a dataset's rows NOW — the same snapshot the render worker uses (GET /datasets/{uid}/data)
// — so the chart config panel can preview the real data. This is the key debugging aid for "does
// this dataset actually link up and return rows?". Subject-scoped datasets bind ?contact_id.
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'datasets:read')) error(403, 'Not permitted');
	const uid = url.searchParams.get('uid');
	if (!uid) error(400, 'Missing dataset uid');
	const contactId = url.searchParams.get('contact_id');
	const qs = contactId ? `?contact_id=${encodeURIComponent(contactId)}` : '';
	const res = await fetch(`${apiUrl()}/datasets/${uid}/data${qs}`, {
		headers: authHeaders(locals.accessToken)
	});
	// Surface the API's real reason (bad SQL, empty result, scope/contact mismatch) — this endpoint
	// is the binding debugging aid, so a fixed generic string tells the author nothing actionable.
	if (!res.ok) error(res.status, await detail(res));
	const data: { columns?: string[]; rows?: unknown[][] } = await res.json();
	return json({ columns: data.columns ?? [], rows: data.rows ?? [] });
};
