import { apiUrl, authHeaders } from '$lib/api';
import type { NotificationPreference } from '@graphene/ui';
import type { PageServerLoad } from './$types';

/** The caller's notification channel preferences (the categories their portal can receive, merged
 * with any overrides). Saving is handled by the sibling `save/+server.ts` proxy. */
export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.accessToken) return { preferences: [] as NotificationPreference[] };
	const res = await fetch(`${apiUrl()}/notifications/preferences`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return { preferences: [] as NotificationPreference[] };
	const body = (await res.json()) as { preferences: NotificationPreference[] };
	return { preferences: body.preferences };
};
