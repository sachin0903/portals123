import { error, redirect } from '@sveltejs/kit';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'contributions:read')) error(403, 'Not authorised');
	return {};
};
