import { redirect } from '@sveltejs/kit';
import { can } from '$lib/permissions';
import type { Member, OrgRole } from '$lib/api';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

type DirectoryPage = { items: Member[]; total: number };

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const mayOperate = can(locals.permissions, locals.role, 'tenant_portal:read');
	const query = url.searchParams.get('q')?.trim() ?? '';

	if (!mayOperate) {
		return {
			profiles: [] as Member[],
			total: 0,
			roles: [] as OrgRole[],
			query,
			loadError: false,
			forbidden: true
		};
	}

	const params: Record<string, string | number | boolean> = {
		kind: 'profile',
		limit: 100,
		offset: 0
	};
	if (query) params.q = query;

	const [profilesRes, rolesRes] = await Promise.all([
		targetJson<DirectoryPage>(fetch, locals.accessToken, tenant.uid, '/firm/members', params),
		targetJson<OrgRole[]>(fetch, locals.accessToken, tenant.uid, '/firm/roles')
	]);

	return {
		profiles: profilesRes.ok ? profilesRes.data.items : [],
		total: profilesRes.ok ? profilesRes.data.total : 0,
		roles: rolesRes.ok ? rolesRes.data.filter((role) => role.portal === 'client') : [],
		query,
		loadError: !profilesRes.ok || !rolesRes.ok,
		forbidden: !profilesRes.ok && profilesRes.status === 403
	};
};
