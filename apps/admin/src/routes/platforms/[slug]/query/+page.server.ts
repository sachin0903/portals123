import { error, redirect } from '@sveltejs/kit';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

// The SQL console's own gate (the six splits — docs/api.md): running read-only SQL against a
// provider's core warehouse is `platforms:query`, split out of `platforms:read`. The [slug]
// layout admits any platforms reader to the OTHER sections (overview, models, runs, lineage,
// streams); this page — and the run proxy beside it — additionally demand the console slug, and
// the nav hides the Query row on the same permission.
export const load: PageServerLoad = ({ locals }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'platforms:query')) error(403, 'Not authorised');
	return {};
};
