import { redirect } from '@sveltejs/kit';
import { loginPath } from '@graphene/auth';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = ({ locals, url }) => {
	// Session expired mid-task: `loginPath` carries a validated same-origin return-to so the
	// user lands back on the page they were using.
	if (!locals.user) redirect(303, loginPath(url));
	return {
		account: {
			email: locals.user.email,
			firstName: locals.user.firstName ?? null,
			lastName: locals.user.lastName ?? null
		}
	};
};
