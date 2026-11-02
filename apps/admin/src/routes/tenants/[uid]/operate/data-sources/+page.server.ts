import { redirect } from '@sveltejs/kit';
import type { DataSource } from '@graphene/ui/book/platforms';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const result = await targetJson<DataSource[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/firm/data-sources'
	);
	return {
		sources: result.ok ? result.data : [],
		loadError: result.ok ? null : 'Could not load this firm’s data sources.'
	};
};
