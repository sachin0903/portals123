/**
 * What a `fill_form` step actually ASKS — the flow editor's window into the form it points at.
 *
 * The step inspector held a dropdown of form NAMES and nothing else, so an author could not see a
 * single question a step asks without leaving the editor for `/forms/[id]`. Forms and flows were
 * two sections that happened to share a uid.
 *
 * Its own door rather than the page loader, for the reason `flows/triggers/+server.ts` has one: the
 * fields belong to whichever form is selected AT THE MOMENT, and a loader would have to fetch every
 * form's fields on every page load to serve a choice the author has not made yet. Lazy and per-uid
 * is both cheaper and always current — a field added in the forms section shows up on the next
 * selection rather than on the next full reload.
 *
 * Read-only. Authoring a form from the flow editor is a separate question (it would need a story
 * for a form two journeys share); this only has to make the questions legible.
 */
import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in.');
	// `forms:read` and not `flows:read`: this serves the FORMS domain's data, and a firm that has
	// withheld the forms section from a role has not implicitly granted it through another page.
	if (!can(locals.permissions, locals.role, 'forms:read')) {
		return json({ fields: [], permitted: false });
	}
	const uid = url.searchParams.get('uid') ?? '';
	if (!uid) error(400, 'No form specified.');
	const res = await fetch(`${apiUrl()}/forms/${encodeURIComponent(uid)}/fields`, {
		headers: authHeaders(locals.accessToken)
	});
	// A form the author has just deleted, or one from another tenant: an empty answer, not a red
	// box. The inspector says "couldn't load" only for a genuine fault.
	if (res.status === 404) return json({ fields: [], permitted: true, missing: true });
	if (!res.ok) error(502, 'Could not load this form’s questions.');
	return json({ fields: await res.json(), permitted: true });
};
