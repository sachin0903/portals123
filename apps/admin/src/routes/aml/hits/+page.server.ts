import { redirect } from '@sveltejs/kit';
import { workHref } from '$lib/aml';
import type { PageServerLoad } from './$types';

/** The hits to review are a filter on the workbench. An old link here lands on it, for the same
 *  firm. Without this route the path would fall to the `[uid]` check page. */
export const load: PageServerLoad = ({ url }) => {
	redirect(308, workHref({ show: 'hits', tenant: url.searchParams.get('tenant')?.trim() ?? '' }));
};
