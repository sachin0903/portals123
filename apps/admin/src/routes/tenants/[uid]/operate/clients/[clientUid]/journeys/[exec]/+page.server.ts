import { error, redirect } from '@sveltejs/kit';
import type { ClientJourneyDetail } from '@graphene/ui';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const result = await targetJson<ClientJourneyDetail>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/crm/contacts/${params.clientUid}/journeys/${params.exec}`
	);
	if (!result.ok) {
		if (result.status === 404) error(404, 'Journey not found.');
		error(result.status || 500, 'Could not load this journey.');
	}
	return { journey: result.data };
};
