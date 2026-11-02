import { error, redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

const apiUrl = () => env.API_URL ?? 'http://127.0.0.1:8000';

export type TenantRow = {
	id: number;
	uid: string;
	alias: string;
	name: string;
	website: string | null;
	enabled: boolean;
	workos_project_id: string | null;
	user_count: number;
	has_environment: boolean;
	has_keys: boolean;
	has_orgs: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The dashboard is a tenants:read surface. A scoped ops user who can't see it is sent
	// to the first section they can, rather than a dead 403.
	if (!can(locals.permissions, locals.role, 'tenants:read')) {
		if (can(locals.permissions, locals.role, 'audit:read')) redirect(303, '/activity');
		if (can(locals.permissions, locals.role, 'operations:read')) redirect(303, '/operations/users');
		if (can(locals.permissions, locals.role, 'model:read')) redirect(303, '/model');
		if (can(locals.permissions, locals.role, 'contributions:read')) redirect(303, '/contributions');
		if (can(locals.permissions, locals.role, 'transfers:read')) redirect(303, '/transfers');
		error(403, 'Not authorised');
	}
	const headers = { Authorization: `Bearer ${locals.accessToken}` };

	let tenants: TenantRow[] = [];
	let apiError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/admin/tenants`, { headers });
		if (res.ok) tenants = await res.json();
		else if (res.status === 403)
			apiError = 'Your account needs a Graphene system/admin role to manage tenants.';
		else apiError = `API responded ${res.status}`;
	} catch {
		apiError = 'The service is unavailable.';
	}

	return { user: { id: locals.user.id, email: locals.user.email }, tenants, apiError };
};
