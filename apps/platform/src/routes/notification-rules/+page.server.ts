import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/**
 * The notification builder moved under Event logs and became the event registry.
 *
 * A permanent redirect, not a deletion: this route was linked to from the nav for months, so
 * bookmarks and any in-app link still point here. The surface is renamed, not withdrawn — the
 * page on the other end answers the same question, event-first.
 *
 * `url.search` is carried so a link that arrived with query state keeps it.
 */
export const load: PageServerLoad = ({ url }) => {
	redirect(301, `/activity/notifications${url.search}`);
};
