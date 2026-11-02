import { redirect } from '@sveltejs/kit';
import type { ActionEvent } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, detail } = await parent();
	const result = await targetJson<{
		items: ActionEvent[];
		has_more: boolean;
		facets?: { event_types: string[]; actors: string[] } | null;
	}>(fetch, locals.accessToken, tenant.uid, '/firm/audit/actions', {
		contact: detail.contact.id,
		limit: PAGE_SIZE,
		facets: true
	});
	return {
		events: result.ok ? result.data.items : [],
		hasMore: result.ok && result.data.has_more,
		facets: result.ok ? result.data.facets : null,
		apiError: result.ok ? null : 'The client event log could not be loaded.'
	};
};
