import { loginPath } from '@graphene/auth';
import { redirect } from '@sveltejs/kit';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = ({ locals, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	return {
		account: {
			email: locals.user.email,
			firstName: locals.user.firstName ?? null,
			lastName: locals.user.lastName ?? null
		}
	};
};
