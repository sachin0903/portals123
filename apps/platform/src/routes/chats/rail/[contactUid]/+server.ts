import { json, type RequestHandler } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { clientTeamRoleLabel, orderClientTeam } from '@graphene/ui';
import { apiUrl, authHeaders } from '$lib/api';
import { railAccounts, railClient, railNotes, type RailDetail } from '$lib/rail';

/**
 * The inbox rail's panels for one client, in ONE round trip — who the conversation is WITH, the
 * advisers on their record, what they still owe, and the tasks open about them.
 *
 * The client block costs nothing: this endpoint already reads `GET /crm/contacts/{uid}` for the
 * advisers and was throwing the rest of that response away, so naming the person the inbox has
 * selected is a field copy rather than a second request.
 *
 * The client record's Messages section gets the same three from its own page load; the inbox
 * cannot, because which client is on screen there changes client-side with the selection. One
 * endpoint rather than three, because they answer one question and a click should cost one
 * request.
 *
 * Addressed by the client's UID, because `GET /crm/contacts/{contact_uid}` is (docs/api.md →
 * "Addressing"). The two listings below are still keyed by the SERIAL, which the CALLER already
 * holds — it rides every conversation row — so it comes in as `contact_id` and all three reads
 * start at once. Taking it from the detail instead made this endpoint as slow as the detail PLUS
 * the slowest listing, for a value we were being handed anyway; the detail is the heaviest read of
 * the three (it carries the accounts, notes, KYC and relationships this panel never shows).
 *
 * Each panel is gated on its OWN permission and SKIPPED (never 403'd) when the reader lacks it, so
 * the panel is absent rather than permanently empty — the posture the record's loader takes. A
 * failed panel is an empty panel for the same reason: this surface is the conversation, and one
 * side panel must not turn into an error over a thread that loaded perfectly.
 */
export const GET: RequestHandler = async ({ locals, fetch, params, url }) => {
	const empty = { advisers: [], requests: [], tasks: [] };
	if (!locals.user) return json(empty);
	if (!can(locals.permissions, locals.role, 'messages:read')) return json(empty);

	const headers = authHeaders(locals.accessToken);
	const contactId = Number(url.searchParams.get('contact_id'));
	const listable = Number.isInteger(contactId) && contactId > 0;

	// Both at once. Each is skipped for a reader without its own permission, and a listing is
	// skipped when the caller sent no serial to key it by — never fetched against a guess.
	const [detailRes, requestsRes] = await Promise.all([
		can(locals.permissions, locals.role, 'contacts:read')
			? fetch(`${apiUrl()}/crm/contacts/${params.contactUid}`, { headers })
			: null,
		listable && can(locals.permissions, locals.role, 'documents:read')
			? fetch(`${apiUrl()}/documents/requests?contact_id=${contactId}`, { headers })
			: null
	]);

	// THE CONTACT IS NESTED, and `$lib/rail` owns both that shape and every mapping off it.
	const detail: RailDetail | null = detailRes?.ok ? await detailRes.json() : null;

	return json({
		// Everything about the CLIENT comes from `$lib/rail`, which the record's Messages section
		// reads too — one derivation, so the same conversation carries the same context wherever it
		// is opened from. `null` when the reader has no `contacts:read`: the panel is then absent
		// rather than a card with three blanks in it.
		client: railClient(detail),
		accounts: railAccounts(detail),
		notes: railNotes(detail),
		// The ROLE on this client (`crm.client_advisers.role`), not the person's job title, named
		// through the one list the record uses (`clientTeamRoleLabel`) so the rail and the record
		// say the same word — Adviser first, then Paraplanner, Administrator or Support.
		advisers: orderClientTeam(detail?.advisers ?? []).map((a) => ({
			name: a.name,
			role: clientTeamRoleLabel(a.role)
		})),
		requests: requestsRes?.ok ? await requestsRes.json() : [],
		tasks: []
	});
};
