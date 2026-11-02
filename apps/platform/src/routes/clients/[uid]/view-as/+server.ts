import { json, type RequestHandler } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, detail, jsonHeaders } from '$lib/api';

/**
 * Open this client's portal as them, and answer with the address to send the new tab to.
 *
 * A thin pass-through: the API decides whether this login may do it — `contacts:read` plus the
 * client being in their own book (or `contacts:read_all`) — mints the session, and returns a
 * single-use ticket together with the firm's client-portal base URL (host-keyed per tenant, so
 * this app does not carry a second copy of it). The only thing assembled here is the URL.
 *
 * The permission check below is the same one the API applies. It is repeated so the button and
 * the request agree, not because it is the gate; the gate is at the API, which has the book.
 */
export const POST: RequestHandler = async ({ locals, params, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'contacts:read')) {
		return json({ error: 'You do not have permission to view a client portal.' }, { status: 403 });
	}

	const res = await fetch(`${apiUrl()}/view-as/open/${params.uid}`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: await request.text()
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	const opened = (await res.json()) as {
		handoff_ticket: string;
		expires_at: string;
		portal_url: string | null;
	};
	const portal = (opened.portal_url ?? '').replace(/\/$/, '');
	if (!portal) {
		return json({ error: 'This firm has no client portal registered to open.' }, { status: 503 });
	}
	return json({
		url: `${portal}/view-as?ticket=${encodeURIComponent(opened.handoff_ticket)}`,
		expiresAt: opened.expires_at
	});
};
