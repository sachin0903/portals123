import { loginPath } from '@graphene/auth';
import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { PageServerLoad } from './$types';

/** The signed-in client's own CRM record, from GET /crm/contacts/{uid} (RLS-scoped, so a client
 *  session only ever gets a party they are linked to). */
type ContactRow = {
	id: number;
	/** The public identifier every `/crm/contacts/{contact_uid}` path takes; `id` is the database's
	 *  own key and only ever a `?contact_id=` filter value. */
	uid: string;
	name: string | null;
	first_name: string | null;
	surname: string | null;
	email: string | null;
	mobile: string | null;
	phone?: string | null;
};

type Address = {
	line1?: string | null;
	line2?: string | null;
	city?: string | null;
	postcode?: string | null;
	country?: string | null;
};

/** GET /crm/contacts/{uid} — the list projection plus the address the profile card prints. */
type ContactDetail = { contact: ContactRow & { address: Address | null } };

/** GET /crm/contacts/mine — every party this login may act for, and which one IS them.
 *  `is_self` is computed from `auth.uid()` (the token's user), not from an email comparison.
 *  `id` is the serial a view-as session names its client by (`locals.viewAs.contactId`). */
type MineContact = { id: number; uid: string; is_self?: boolean };
type Mine = { contacts?: MineContact[] | null };

export type ClientProfile = {
	firstName: string | null;
	lastName: string | null;
	email: string | null;
	mobilePhone: string | null;
	phone: string | null;
	address: Address | null;
};

function profileFrom(contact: ContactDetail['contact']): ClientProfile {
	return {
		firstName: contact.first_name,
		lastName: contact.surname,
		email: contact.email,
		mobilePhone: contact.mobile,
		phone: contact.phone ?? null,
		address: contact.address
	};
}

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	const headers = authHeaders(locals.accessToken);

	// The client's real record. There is deliberately NO fixture fallback: showing invented contact
	// details on someone's own account page is indistinguishable, to them, from another client's
	// data leaking in (docs/audit/06-core.md → "Seed / demo data strategy").
	//
	// Which party is *them* is `is_self` on GET /crm/contacts/mine, computed from `auth.uid()` —
	// the user id on the access token. It is not the session email. A normal password login and a
	// WorkOS impersonation of that same person share the token subject, so onboarding and the rest
	// of the portal already see their book; the sealed session's `user.email` does not have to
	// equal `auth.users.email` (the mirror a self-link is joined to). Matching on that address
	// failed closed during impersonation and the page showed the WorkOS name with no phone or
	// address. A party they merely act for (power of attorney, a delegate link) is never a
	// substitute: `is_self` is false for those, and we do not fall through to another row.
	//
	// A staff view-as session is the exception, and it is not a fallback. The token keeps the
	// adviser's user id (the audit actor), so `is_self` is false for the client on screen, and
	// the shell's stand-in user is just that client's name — which is what this page then printed
	// as the first name and the email, with no phone or address. The session is already narrowed
	// to one client (`locals.viewAs.contactId`, the sole id in `contact_ids`). That client is who
	// the page is about. An ordinary login never takes this branch.
	const mine = await fetchJson<Mine>(fetch, `${apiUrl()}/crm/contacts/mine`, headers);
	if (!mine.ok) return { profile: null, loadError: true, unresolved: false };

	const contacts = mine.data.contacts ?? [];
	const viewedId = locals.viewAs?.contactId;
	const self =
		viewedId != null
			? contacts.find((contact) => contact.id === viewedId && contact.uid)
			: contacts.find((contact) => contact.is_self === true && contact.uid);
	if (!self) return { profile: null, loadError: false, unresolved: true };

	const detail = await fetchJson<ContactDetail>(
		fetch,
		`${apiUrl()}/crm/contacts/${self.uid}`,
		headers
	);
	if (!detail.ok) return { profile: null, loadError: true, unresolved: false };
	return { profile: profileFrom(detail.data.contact), loadError: false, unresolved: false };
};

// No form actions: `crm.contacts` grants the `authenticated` role SELECT only (0002_platform_crm),
// and the only write path — PATCH /firm/contacts/{id} — requires the staff `contacts:write`
// permission. So there is nothing a client session could persist. The page renders these fields
// read-only and says so, rather than accepting edits and silently discarding them (which is what
// the previous "Demo only" action did).
