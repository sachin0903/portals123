import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/**
 * The client-record section was renamed Screening → Compliance. A permanent redirect, not a
 * deletion: bookmarks and in-app links still pointed here.
 */
export const load: PageServerLoad = ({ params }) => {
	redirect(301, `/clients/${params.uid}/compliance`);
};
