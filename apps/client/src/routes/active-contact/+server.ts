import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { ACTIVE_CONTACT_COOKIE, cookieOptions } from '$lib/server/workos';
import type { RequestHandler } from './$types';

/** Sets the active-contact cookie for a login linked to more than one crm.contacts row (the
 * nav bar's client switcher). Re-checks the chosen id against `/crm/contacts/mine` before
 * writing it — not the security boundary (every downstream API call re-validates against
 * auth.contact_scope() itself regardless), just so a bad id here 400s cleanly rather than
 * silently setting a cookie the next page load will just fall back away from anyway. */
export const POST: RequestHandler = async ({ locals, fetch, request, cookies }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const { contactId } = await request.json();
	const id = Number(contactId);
	if (!Number.isInteger(id)) return json({ error: 'Invalid contact id.' }, { status: 422 });

	const res = await fetch(`${apiUrl()}/crm/contacts/mine`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: 'Could not verify that client.' }, { status: 502 });
	const body: { contacts: { id: number; name: string }[] } = await res.json();
	if (!body.contacts.some((c) => c.id === id)) {
		return json({ error: 'Not one of your linked clients.' }, { status: 403 });
	}

	cookies.set(ACTIVE_CONTACT_COOKIE, String(id), cookieOptions);
	return json({ ok: true });
};
