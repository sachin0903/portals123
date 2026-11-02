import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

// The per-action page is the runs workbench filtered to one action; this route only keeps the
// old address working (see the platform's `actions/[key]`).
export const load: PageServerLoad = async ({ params }) => {
	redirect(303, `/tenants/${params.uid}/operate/actions?action=${encodeURIComponent(params.key)}`);
};
