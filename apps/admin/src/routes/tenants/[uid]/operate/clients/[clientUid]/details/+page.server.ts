import { redirect } from '@sveltejs/kit';
import { loadOperateClientRecord } from '$lib/operate-client-record-load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, detail: shell } = await parent();
	return {
		record: loadOperateClientRecord(
			fetch,
			locals.accessToken,
			tenant.uid,
			params.clientUid,
			shell.contact.id
		)
	};
};
