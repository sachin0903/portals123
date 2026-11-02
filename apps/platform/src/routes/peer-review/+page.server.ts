import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

// The event is unused. It stays so the function's arity matches `PageServerLoad`:
// omitting it makes a required argument look superfluous.
export const load: PageServerLoad = (event) => {
	void event;
	redirect(308, '/actions');
};
