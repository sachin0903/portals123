import { error, redirect } from '@sveltejs/kit';
import type { ClientJourneySummary } from '@graphene/ui';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const result = await targetJson<{ journeys: ClientJourneySummary[] }>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/crm/contacts/${params.clientUid}/journeys`
	);
	if (!result.ok) error(result.status || 500, 'Could not load journeys.');
	return {
		journeys: result.data.journeys ?? [],
		newJourneyHref: writing ? `/tenants/${tenant.uid}/operate/journeys/new` : undefined
	};
};
