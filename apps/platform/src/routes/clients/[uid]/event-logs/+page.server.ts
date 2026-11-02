import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** `/clients/[uid]/event-logs` is now `/clients/[uid]/activity` - renamed with the firm-wide surface. */
export const load: PageServerLoad = ({ params, url }) => {
	redirect(301, `/clients/${params.uid}/activity${url.search}`);
};
