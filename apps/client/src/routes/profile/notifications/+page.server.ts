import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { NotificationPreference } from '@graphene/ui';
import type { PageServerLoad } from './$types';

/** The client's own notification preferences (contact-keyed; RLS scopes the write to their
 * contacts). The API returns the categories addressed to them merged with the shipped defaults.
 *
 * A failed read is a `loadError`, not an empty list: the editor renders every category as "off"
 * when it is given nothing, and a client who then presses Save would write those blanks over
 * preferences they never chose to change. */
export const load: PageServerLoad = async ({ locals, fetch, depends }) => {
	depends('app:notification-preferences');
	if (!locals.accessToken) return { preferences: [] as NotificationPreference[], loadError: false };
	const res = await fetchJson<{ preferences: NotificationPreference[] }>(
		fetch,
		`${apiUrl()}/notifications/client-preferences`,
		authHeaders(locals.accessToken)
	);
	if (!res.ok) return { preferences: [] as NotificationPreference[], loadError: true };
	return { preferences: res.data.preferences ?? [], loadError: false };
};
