import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ locals, params }) => {
	if (!locals.user) redirect(303, '/login');
	redirect(308, `/tenants/${params.uid}/operate/actions`);
};
