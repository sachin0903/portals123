import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** `/book` itself has no content — the section opens on the clients listing, like the platform
 *  portal's own landing order (clients are the book). 307 so the bare URL stays shareable. */
export const load: PageServerLoad = ({ params }) => {
	redirect(307, `/tenants/${params.uid}/book/clients`);
};
