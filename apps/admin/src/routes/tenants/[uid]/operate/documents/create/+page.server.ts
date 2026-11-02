import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ params, url }) => {
	const query = url.searchParams.toString();
	redirect(307, `/tenants/${params.uid}/operate/documents/generate${query ? `?${query}` : ''}`);
};
