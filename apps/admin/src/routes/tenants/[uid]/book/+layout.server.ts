import { error, redirect } from '@sveltejs/kit';
import { can } from '$lib/permissions';
import type { LayoutServerLoad } from './$types';

/**
 * The gate on the whole book section. `tenants:read` (the [id] layout's gate) lets an operator
 * ADMINISTER a firm; reading the firm's clients and money is its own operations permission,
 * `book:read` — so a direct URL without it errors here rather than rendering a shell whose every
 * fetch 403s. The API enforces the same gate regardless (`book_session` refuses the `?tenant=`
 * param without it); this is the UX half, mirroring how the other admin sections gate.
 *
 * `writing` is whether the operator ALSO holds `book:write` — the client record uses it to mount
 * the middle-office actions, and the banner uses it to say which mode the operator is in. It is a
 * display flag only: every write is re-gated in the form actions and again at the API.
 */
export const load: LayoutServerLoad = async ({ locals, parent }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'book:read'))
		error(403, 'Not authorised to view tenant books.');
	// The tenant (id, uid, name) is already loaded by the [id] layout — surfacing it from here
	// costs nothing and lets every book loader read it off `parent()` without re-fetching.
	const { tenant } = await parent();
	return { tenant, writing: can(locals.permissions, locals.role, 'book:write') };
};
