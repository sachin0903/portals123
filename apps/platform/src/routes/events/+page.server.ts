import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/**
 * `/events` is now `/activity`.
 *
 * The name was the problem — "Events" reads as a product concept and the page is a log — but the
 * page itself is unchanged, so an old link should land on it rather than on a 404. Deliberately a
 * redirect and NOT a second surface: the hazard `docs/events-rework.md` records is a route that
 * means two different things across the codebase's history.
 */
export const load: PageServerLoad = ({ url }) => {
	redirect(301, `/activity${url.search}`);
};
