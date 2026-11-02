import { error, json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

// Render the CURRENT editor schema against sample data — the same pipeline the worker uses (Jinja +
// chart/table substitution), minus WeasyPrint — so the editor's Preview shows a real, data-filled
// document (charts, tables and merge tokens rendered) rather than the raw canvas.
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'templates:read')) error(403, 'Not permitted');
	const body = (await request.json().catch(() => ({}))) as { schema?: unknown };
	const res = await fetch(`${apiUrl()}/templates/${params.uid}/preview`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ schema: body?.schema ?? {} })
	});
	if (!res.ok) error(res.status, 'Could not render preview');
	const data = (await res.json()) as {
		html: string | null;
		unknown_variables?: { name: string; suggestion?: string | null }[];
	};
	return json({ html: data.html ?? null, unknownVariables: data.unknown_variables ?? [] });
};
