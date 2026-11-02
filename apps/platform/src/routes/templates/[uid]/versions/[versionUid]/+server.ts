import { error, json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Returns one template version's saved editor blob, so the editor's history panel can load an
// older/other version on demand (rather than preloading every version's blob up front).
export const GET: RequestHandler = async ({ params, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'templates:read')) error(403, 'Not permitted');
	const res = await fetch(`${apiUrl()}/templates/${params.uid}/versions/${params.versionUid}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Version not found');
	const detail = await res.json();
	return json({ schema: detail.schema ?? null });
};
