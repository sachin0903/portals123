import { error, redirect } from '@sveltejs/kit';
import { can } from '$lib/permissions';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ locals, parent }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'tenant_portal:read')) {
		error(403, 'Not authorised to operate tenant portals.');
	}

	const { tenant } = await parent();
	return {
		tenant,
		writing: can(locals.permissions, locals.role, 'tenant_portal:write'),
		canBook: can(locals.permissions, locals.role, 'book:read'),
		canAudit: can(locals.permissions, locals.role, 'audit:read'),
		canPlatforms: can(locals.permissions, locals.role, 'platforms:read')
	};
};
