import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { ClientJourneyDetail } from '@graphene/ui';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, fetch }) => {
	if (!can(locals.permissions, locals.role, 'cases:read')) error(403, 'Not authorised');

	const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/journeys/${params.exec}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (res.status === 404) error(404, 'Journey not found.');
	if (!res.ok) error(res.status || 500, 'Could not load this journey.');
	return { journey: (await res.json()) as ClientJourneyDetail };
};
