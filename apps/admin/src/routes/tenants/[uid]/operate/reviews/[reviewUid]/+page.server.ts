import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** Old deep links land on the table with that row open. */
export const load: PageServerLoad = async ({ params }) => {
	redirect(
		303,
		`/tenants/${params.uid}/operate/reviews?open=${encodeURIComponent(params.reviewUid)}`
	);
};
