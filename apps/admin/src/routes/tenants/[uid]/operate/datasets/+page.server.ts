import { redirect } from '@sveltejs/kit';
import { toDataset, type ApiDataset } from '@graphene/ui/dataset-authoring';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const r = await targetJson<ApiDataset[]>(fetch, locals.accessToken, tenant.uid, '/datasets');
	return {
		tenantUid: tenant.uid,
		datasets: r.ok ? r.data.map(toDataset) : [],
		canManage: writing,
		loadError: !r.ok
	};
};
