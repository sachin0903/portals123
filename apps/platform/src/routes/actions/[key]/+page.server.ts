import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

// The per-action page is the runs workbench filtered to one action. It used to be a second
// component with its own copy of the step inspector and its own status map; the one listing now
// takes `?action=`, so this route only keeps the old address working.
export const load: PageServerLoad = async ({ params }) => {
	redirect(303, `/actions?action=${encodeURIComponent(params.key)}`);
};
