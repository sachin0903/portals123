import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** The section has no landing page of its own — Products is the first item. */
export const load: PageServerLoad = ({ params }) => {
	redirect(307, `/tenants/${params.uid}/platform/products`);
};
