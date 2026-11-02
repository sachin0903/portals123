import { clientName, serviceTypeLabel } from '@graphene/ui';
import type { RailAccount, RailClient, RailNote } from '@graphene/ui/book/messages';

/**
 * The conversation rail's client panels, built from `GET /crm/contacts/{uid}`.
 *
 * ONE derivation, read by both surfaces that mount the rail: the inbox, through
 * `/chats/rail/{contact_uid}`, and the client record's Messages section, from its own page load.
 * They were two copies for a while and the record's was the poorer one — no client block and no
 * accounts at all — which made the same conversation carry different context depending on where it
 * was opened from. A rail that disagrees with itself is worse than a rail with less on it.
 *
 * THE CONTACT IS NESTED. `GET /crm/contacts/{uid}` answers `{ contact, advisers, accounts, notes }`
 * — the person's own columns live under `contact`, not at the top level. Reading them off the root
 * is what put "Client" (the last fallback in `clientName`) at the head of the rail with no email,
 * no phone and no reference: every field was `undefined`, so nothing threw and nothing was right.
 */
export type RailDetail = {
	contact: {
		type: 'individual' | 'organisation';
		sub_type: string | null;
		service_type: string | null;
		provider_status: string | null;
		deleted_at: string | null;
		uid: string | null;
		name: string | null;
		title: string | null;
		first_name: string | null;
		surname: string | null;
		email: string | null;
		mobile: string | null;
		phone: string | null;
		dob: string | null;
		address: {
			line1?: string | null;
			line2?: string | null;
			city?: string | null;
			postcode?: string | null;
		} | null;
	} | null;
	accounts?: {
		uid: string;
		name: string | null;
		product_type: string | null;
		value: number | null;
		growth: number;
	}[];
	notes?: {
		id: number;
		/** The note's public handle — what `DELETE /firm/contacts/{uid}/notes/{note_uid}` takes. */
		uid?: string | null;
		body: string;
		author: string | null;
		created_at: string;
	}[];
	advisers?: { name: string | null; role: string | null }[];
};

/**
 * WHO THE CONVERSATION IS WITH, and the facts an adviser would otherwise open the record for.
 *
 * `clientName`, NEVER the bare `name` column. `crm.contacts.name` is set for an ORGANISATION and
 * null for an individual, whose display name is composed from title/first/surname — so reading the
 * column straight put "Unknown" at the head of the rail on every human being in the book. Every
 * label here goes through the helper that owns that vocabulary for the same reason: a second
 * spelling of "Discretionary" or "Onboarding" would disagree with the record it is summarising.
 */
export function railClient(detail: RailDetail | null): RailClient | null {
	const contact = detail?.contact ?? null;
	if (!contact) return null;
	// NO REFERENCE AND NO STATUS. The rail draws neither — a provider reference is a filing code
	// for talking to a custodian, and "Active" beside a live conversation is the one fact nobody
	// opens a thread to read — so computing them here was a `clientStatus` call and a string
	// walk per rail read for fields that reached the markup and were dropped.
	return {
		name: clientName(contact),
		kind: contact.type,
		email: contact.email,
		phone: contact.mobile ?? contact.phone,
		serviceType: serviceTypeLabel(contact.service_type),
		dob: contact.dob,
		// WITHOUT THE COUNTRY, deliberately. The record's line resolves an ISO code against
		// `ref.countries`, and that formatter is private to `client-record.svelte` — a second copy
		// of it here is the drift the design language bans. A summary beside a thread wants the
		// postal line; the record stays the place that states it in full.
		address:
			[
				contact.address?.line1,
				contact.address?.line2,
				contact.address?.city,
				contact.address?.postcode
			]
				.map((part) => (part ?? '').trim())
				.filter(Boolean)
				.join(', ') || null
	};
}

/** WHAT THEY HOLD, summarised — the wrapper and its value, which is enough to answer "what is this
 *  conversation about" without opening the record. */
export function railAccounts(detail: RailDetail | null): RailAccount[] {
	return (detail?.accounts ?? []).map((a) => ({
		uid: a.uid,
		name: a.name,
		productType: a.product_type,
		value: a.value,
		growth: a.growth
	}));
}

/** The RECENT notes, newest first. The record holds the whole history; the rail answers "what do
 *  we already know about them" without leaving the conversation.
 *
 *  THE UID, not the serial. The rail's `uid` is what its delete sends back to
 *  `DELETE /firm/contacts/{uid}/notes/{note_uid}`, which matches on `crm.contact_notes.uid` — a
 *  serial there was a note that could be listed but never removed (every delete answered 404).
 *  The serial is only the fallback for a detail read that predates the column. */
export function railNotes(detail: RailDetail | null, limit = 5): RailNote[] {
	return [...(detail?.notes ?? [])]
		.sort((a, b) => b.created_at.localeCompare(a.created_at))
		.slice(0, limit)
		.map((n) => ({
			uid: n.uid ?? String(n.id),
			body: n.body,
			author: n.author,
			createdAt: n.created_at
		}));
}
