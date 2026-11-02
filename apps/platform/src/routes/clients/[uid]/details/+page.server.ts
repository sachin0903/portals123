import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, type ContactDetail } from '$lib/api';
import { loadClientBookPin } from '$lib/classification';
import { loadClientGroups } from '$lib/client-groups';
import { can } from '@graphene/api-client/permissions';
import { justificationKeys, type ReviewPolicy, type ReviewRow } from '@graphene/ui/book/reviews';
import type { PageServerLoad } from './$types';

/**
 * The record's Details band: the person themselves (contact, bank, personal, pension, employer).
 *
 * The layout supplies only the shell. This load fetches the fat record — bank, FATCA, the
 * person fields the forms edit — and the client-book pin the header draws. Members, models and
 * countries are NOT read here: the pickers fetch them when a drawer or popout opens (`/pickers/…`).
 *
 * Mutations POST to the overview page's actions (an absolute `/clients/{uid}?/updateClient` etc.,
 * built in `client-record-page.svelte`) so they are not written twice.
 */
export const load: PageServerLoad = async ({ locals, params, fetch }) => {
	if (!locals.user) redirect(303, '/login');

	const canListBooks =
		can(locals.permissions, locals.role, 'contacts:write') ||
		can(locals.permissions, locals.role, 'contacts:read');
	// The fat record is STREAMED so the Details shape is on screen the moment the layout has
	// named the client. `redirect` stays out here.
	return { record: loadRecord(locals.accessToken, fetch, params.uid, canListBooks) };
};

async function loadRecord(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string,
	canListBooks: boolean
) {
	const headers = authHeaders(accessToken);
	const [res, pendingRes, policiesRes, pin, membership] = await Promise.all([
		fetch(`${apiUrl()}/crm/contacts/${uid}`, { headers }),
		fetch(`${apiUrl()}/reviews/pending?contact_uid=${uid}`, { headers }),
		fetch(`${apiUrl()}/reviews/policies`, { headers }),
		loadClientBookPin(uid, headers, fetch, { canListBooks }),
		loadClientGroups(uid, headers, fetch, { canList: canListBooks })
	]);
	if (res.status === 404) error(404, 'Client not found');
	if (!res.ok) error(res.status, 'Could not load client');
	const detail: ContactDetail = await res.json();
	return {
		detail,
		pendingReviews: pendingRes.ok ? ((await pendingRes.json()) as ReviewRow[]) : [],
		justificationRequired: policiesRes.ok
			? justificationKeys((await policiesRes.json()) as ReviewPolicy[])
			: [],
		...pin,
		...membership
	};
}
