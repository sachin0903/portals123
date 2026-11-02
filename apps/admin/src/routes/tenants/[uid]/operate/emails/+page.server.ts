import { redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { CommsFacets, CommsMessage } from '@graphene/ui/comms';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';
export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const eventType = (url.searchParams.get('event_type') ?? '').trim().slice(0, 100);
	const q: Record<string, string | number> = { limit: PAGE_SIZE, facets: 'true' };
	if (eventType) q.event_type = eventType;
	const result = await targetJson<{
		items: CommsMessage[];
		has_more: boolean;
		facets?: CommsFacets | null;
		sending_disabled?: string | null;
	}>(fetch, locals.accessToken, tenant.uid, '/firm/comms/messages', q);
	return {
		tenantUid: tenant.uid,
		messages: result.ok ? result.data.items : [],
		facets: result.ok ? (result.data.facets ?? null) : null,
		hasMore: result.ok ? result.data.has_more : false,
		// A flag, never the reason: it names configuration, and page data is serialised into the HTML.
		sendingDisabled: result.ok && Boolean(result.data.sending_disabled),
		eventType,
		apiError: result.ok ? null : 'Could not load sent email.'
	};
};
