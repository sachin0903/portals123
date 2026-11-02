import { json, error } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { RequestHandler } from './$types';

/**
 * A same-origin proxy for the team member page's custodian-staff picker.
 *
 * The picker types into a `fetch` from the BROWSER, and the browser holds no API access token —
 * every other data path on this page is a server `load` or a form action. So this is the same
 * shape the other client-side searches in the estate use: a `+server.ts` that re-checks the
 * permission and forwards with the session's own token, never handing that token to the page.
 *
 * `members:read` and not `members:write`: choosing a mapping is a write, but LOOKING at the
 * custodian's directory is a read, and the save is gated on its own.
 */
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'members:read')) error(403, 'Not permitted');

	const provider = url.searchParams.get('provider') ?? '';
	const q = url.searchParams.get('q') ?? '';
	if (!provider) error(400, 'A provider is required');

	const res = await fetch(
		`${apiUrl()}/firm/members/provider-staff/${encodeURIComponent(provider)}` +
			`?q=${encodeURIComponent(q)}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	// An unreadable warehouse comes back as `{staff: [], unavailable: "…"}` with a 200 — the
	// picker renders no matches and the operator can still type an id. A hard failure here would
	// break the drawer for a reason that has nothing to do with the person being edited.
	if (!res.ok)
		return json({ staff: [], unavailable: `provider staff unavailable (${res.status})` });
	return json(await res.json());
};
