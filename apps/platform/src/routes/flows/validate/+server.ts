/**
 * Live publish validation for the flow builder — the browser's door to `POST /flows/{uid}/validate`.
 *
 * A `+server.ts` rather than a form action because this is a QUESTION asked continuously while the
 * author types, not a submission: no navigation, no invalidation, no toast. The access token lives
 * in the server-side session and must never reach the canvas, so the browser posts here and this
 * relays.
 *
 * It sits at `/flows/validate` rather than under `/flows/[uid]/` on purpose — a static segment wins
 * over the dynamic one in SvelteKit's router, and flow uids are `flw_…`, so nothing is shadowed —
 * and it keeps the builder's page route untouched. The flow uid rides in the body with the draft.
 *
 * The gate is re-checked here. A form action or an endpoint is a public URL as far as the browser
 * is concerned; "the canvas was read-only" is not an authorisation control.
 */
import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in.');
	if (!can(locals.permissions, locals.role, 'flows:write')) {
		error(403, 'You do not have permission to edit journeys.');
	}
	let body: { flow?: unknown; definition?: unknown };
	try {
		body = await request.json();
	} catch {
		error(400, 'Malformed request.');
	}
	const flow = typeof body.flow === 'string' ? body.flow : '';
	if (!flow) error(400, 'No journey specified.');

	const res = await fetch(`${apiUrl()}/flows/${encodeURIComponent(flow)}/validate`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ definition: body.definition ?? {} })
	});
	// An upstream failure is relayed as a failure, never as "no issues" — the builder reads an
	// empty list as "this would publish" and clears every badge (see validation.ts).
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Could not check this journey.');
	return json(await res.json());
};
