import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/**
 * `/events` is now `/activity`.
 *
 * The surface became two tabs - Actions and Audit - so "event logs" named only half of what it
 * holds. The page itself is unchanged, so an old link, a bookmark or a notification still lands on
 * it rather than on a 404. Deliberately a redirect and NOT a second surface: the hazard
 * `docs/events-rework.md` records is a route that means two different things across the codebase's
 * history.
 */
export const load: PageServerLoad = ({ url }) => {
	redirect(301, `/activity${url.search}`);
};
