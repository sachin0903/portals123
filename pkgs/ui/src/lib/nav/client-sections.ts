/**
 * The client record's sections — the strip that used to be a `Tabs.Root` on `/clients/[uid]`.
 *
 * Each section is now a route, and the SIDEBAR is the strip: `clients/[uid]/+layout.server.ts`
 * publishes `sidebarFocus` and the shell renders it as a focused nav (the same mechanism the admin
 * portal uses to focus on a tenant). Icons travel as keys into `FOCUS_ICONS` because `page.data`
 * is serialised — a component reference cannot cross that boundary.
 *
 * The maps below have to agree, which is why they live in one file: `clientSections` is where a reader
 * can go, and `clientTabTarget` is where the old `?tab=` deep links land. A section added to one and
 * forgotten in the other is a dead link, and `client-sections.test.ts` fails on it.
 */

import type { FocusSection, SidebarFocus } from './sidebar-focus';

/** What the sidebar needs to know about the client it is focused on. */
export interface ClientFocusInput {
	/** Mounted under a path other than the root — admin's operate-as-firm view passes
	 *  `/tenants/{uid}/operate`, so its sidebar is this one, not a copy. */
	prefix?: string;
	/** Where "up" goes. Defaults to the dashboard at `prefix`. */
	back?: { href: string; label: string };
	/** The client's `uid` — every section href is built from it. Deliberately not the serial
	 *  `crm.contacts.id`, which is the database's key and never appears in a URL. */
	uid: string;
	name: string;
	email?: string | null;
	/**
	 * `documents:read`. Gates the Documents section (the loader resolved no document data at all
	 * without it) — a nav row that 403s on click is worse than one that isn't there.
	 */
	canReadDocuments: boolean;
	/**
	 * Documents read or illustrations write. The section lists filed illustrations and hosts the
	 * generate form; either permission is enough to see it, and the page 403s otherwise.
	 */
	canSeeIllustrations?: boolean;
	/**
	 * `activity:read`. Gates BOTH of this client's feed sections, the way the one permission gates
	 * both firm-wide surfaces: Activity (the outbound email sent to them, `GET
	 * /firm/comms/messages?contact=`) and Event logs (their slice of the action-event stream, `GET
	 * /firm/audit/actions?contact=`). Each is scoped by the record's uid server-side, so neither can
	 * answer with another client's — or the firm's.
	 */
	canReadActivity?: boolean;
	/**
	 * `screening:read`. Gates the Compliance section — a firm's read-only window onto Graphene's
	 * AML/identity review of this client. Without the permission the section is absent rather than
	 * present-and-403, same rule as Documents.
	 */
	canReadCompliance?: boolean;
	/**
	 * `cases:read`. Gates the Journeys section — the journeys this client is on, and the forms
	 * they answered. Absent rather than present-and-403, same rule as Documents.
	 */
	canReadJourneys?: boolean;
	/**
	 * `messages:read`. Gates the Messages section — the same client<->adviser conversation the
	 * platform inbox's Direct Messages shows, viewed from the client's own record instead.
	 * Absent rather than present-and-403, same rule as Documents/Compliance.
	 */
	canReadMessages?: boolean;
	/** Whether THIS client's own conversation is unread (never the tenant-wide signal the
	 * global nav's Messages item carries) — see `GET /messaging/unread?contact_id=`. */
	chatsUnread?: boolean;
	/** `crm.contacts.id` — never the `uid` above. Published as `liveUnreadContactId` so the root
	 *  layout can keep the Messages dot fresh itself; absent (e.g. a caller that never fetched the
	 *  contact) just means the dot stays whatever `chatsUnread` seeded it to. */
	contactId?: string;
}

/**
 * The record's sections, in sidebar order.
 *
 * Overview is the landing view and carries accounts, a copy of personal details, and notes.
 * Details is the person themselves (contact, personal, pension, employer) with the same chrome.
 * The sections here are those two plus the deeper surfaces.
 */
export function clientSections(input: ClientFocusInput): FocusSection[] {
	const base = `${input.prefix ?? ''}/clients/${input.uid}`;
	const docs = input.canReadDocuments;
	return [
		{ label: 'Overview', href: base, icon: 'overview', exact: true },
		{ label: 'Details', href: `${base}/details`, icon: 'details' },
		...(input.canReadJourneys
			? [{ label: 'Journeys', href: `${base}/journeys`, icon: 'journeys' }]
			: []),
		{ label: 'Transactions', href: `${base}/transactions`, icon: 'transactions' },
		...(docs ? [{ label: 'Documents', href: `${base}/documents`, icon: 'documents' }] : []),
		...(input.canSeeIllustrations
			? [{ label: 'Illustrations', href: `${base}/illustrations`, icon: 'illustrations' }]
			: []),
		...(input.canReadCompliance
			? [{ label: 'Compliance', href: `${base}/compliance`, icon: 'compliance' }]
			: []),
		...(input.canReadActivity
			? [
					// Two feeds, one permission, and deliberately two rows: Activity is the email that
					// left the building and what the provider did with it; Event logs is the action
					// trail. The firm-wide surfaces are split the same way for the same reason — "we
					// recorded that a document was sent" and "it was delivered at 14:02" are different
					// answers, and folding them into one tab loses the second (docs/comms.md).
					{ label: 'Emails', href: `${base}/emails`, icon: 'emails' },
					{ label: 'Activity', href: `${base}/activity`, icon: 'activity' }
				]
			: []),
		...(input.canReadMessages
			? [
					{
						label: 'Messages',
						href: `${base}/chat`,
						icon: 'chat',
						unread: input.chatsUnread ?? false
					}
				]
			: [])
	];
}

/** The whole focused-sidebar payload for a client: context header + sections. */
export function clientSidebarFocus(input: ClientFocusInput): SidebarFocus {
	return {
		eyebrow: 'Client',
		title: input.name,
		...(input.email ? { subtitle: input.email } : {}),
		// Out of the record and back to the hub. The dashboard now carries the book (clients, stats,
		// action items), so it — not the bare list — is what "up" means from a client.
		back: input.back ?? { href: input.prefix || '/', label: 'Dashboard' },
		sections: clientSections(input),
		...(input.canReadMessages && input.contactId ? { liveUnreadContactId: input.contactId } : {})
	};
}

/**
 * The old `?tab=` values that became ROUTES, and the route each one became.
 *
 * These links are in the wild — the dashboard's action-items rail points at `?tab=documents` — so the
 * record's load redirects them rather than quietly dropping the reader on the overview.
 *
 * `people` is absent: the record's own key and gear open the advisers and the
 * portal logins as popouts now, so the page that held them both is gone. A `?tab=people` link in
 * the wild lands on the record, where the two doors are.
 */
export const CLIENT_TAB_ROUTES: Record<string, string> = {
	transactions: '/transactions',
	documents: '/documents',
	illustrations: '/illustrations',
	details: '/details',
	journeys: '/journeys',
	// A SUFFIX under `/clients/{id}`, not an absolute route: it resolves to this client's own
	// action trail at `/clients/con_9b2e/activity`. Renamed with the firm-wide one — the surface is called
	// Logs everywhere now, because "Events" is the notification registry.
	activity: '/activity',
	// The section was Screening; the page is Compliance now. Old `?tab=screening` links land there.
	screening: '/compliance'
};

/**
 * The old `?tab=` values that FOLDED INTO the landing view, and the `id` of the band each one became.
 *
 * These don't redirect: the deep link already points at the right page, so the landing view brings the
 * band it named into view instead. (A `#hash` in the redirect's `Location` would have been neater, but
 * the fragment does not survive the round trip — verified against the running app, not assumed.)
 */
export const CLIENT_TAB_ANCHORS: Record<string, string> = {
	accounts: 'accounts',
	notes: 'notes'
};

/** Either "send them to this route" or "they're on the right page — scroll to this band". */
export type TabTarget = { redirect: string } | { anchor: string };

/**
 * Where an old `?tab=` deep link should end up.
 *
 * An unknown, empty or retired tab redirects to the clean record URL: the reader asked for this
 * client, and the worst outcome is landing at the top of it — never a 404, and never a stale query
 * string left behind.
 */
export function clientTabTarget(uid: string, tab: string | null | undefined): TabTarget {
	const key = (tab ?? '').trim().toLowerCase();
	const route = CLIENT_TAB_ROUTES[key];
	if (route) return { redirect: `/clients/${uid}${route}` };
	const anchor = CLIENT_TAB_ANCHORS[key];
	if (anchor) return { anchor };
	return { redirect: `/clients/${uid}` };
}
