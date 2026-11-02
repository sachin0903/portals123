import { error, redirect } from '@sveltejs/kit';
import type { ActionEvent } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { can } from '$lib/permissions';
import { targetRequest } from '$lib/operate';
import type { PageServerLoad } from './$types';

type ActionFacets = { event_types: string[]; actors: string[] };

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'audit:read')) error(403, 'Not authorised');

	const response = await targetRequest(
		fetch,
		locals.accessToken,
		params.uid,
		'/admin/audit/actions',
		{},
		{ limit: PAGE_SIZE, facets: true }
	);
	if (!response.ok) {
		return {
			events: [] as ActionEvent[],
			facets: null as ActionFacets | null,
			hasMore: false,
			apiError:
				response.status === 403
					? 'audit:read required to view the event stream.'
					: 'The event stream could not be loaded.',
			tenantUid: params.uid
		};
	}
	const page = (await response.json()) as {
		items: ActionEvent[];
		has_more: boolean;
		facets?: ActionFacets | null;
	};
	return {
		events: page.items,
		facets: page.facets ?? null,
		hasMore: page.has_more,
		apiError: null,
		tenantUid: params.uid
	};
};
