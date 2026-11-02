/**
 * Event-stream entity → this portal's own record page.
 *
 * Shared by every Events surface in the firm portal (the firm-wide trail and the per-client one),
 * because a link that works on one of them and not the other is a bug a reader meets by accident.
 * The detail pane also picks its "Open …" button from the first entity this can route, so a kind
 * missing here is a button that does not appear.
 *
 * ADDRESSED BY UID, never the serial the stream recorded: that serial is history, and these routes
 * are uid-typed. An entity the API could not resolve carries no uid, so there is nothing to link.
 */
import type { ActionEntity } from '@graphene/ui';

const ROUTES: Record<string, (uid: string) => string> = {
	contact: (uid) => `/clients/${uid}`,
	account: (uid) => `/accounts/${uid}`,
	document: (uid) => `/documents/${uid}`,
	template: (uid) => `/templates/${uid}`,
	form: (uid) => `/forms/${uid}`,
	flow: (uid) => `/flows/${uid}`,
	user: (uid) => `/team/${uid}`,
	// Added with the September 2026 event-content review, which gave these kinds resolvable names.
	// ONLY the kinds this portal has a real page for: a link to a route that does not exist is
	// worse than no link, because the chip invites the click and the reader gets a 404 they then
	// read as the record having been deleted.
	journey: (uid) => `/journeys/${uid}`,
	content: (uid) => `/content/${uid}`,
	automation: (uid) => `/automations/${uid}`,
	document_request: (uid) => `/documents/requests/${uid}`,
	// The workbenches are flat listings, so the record is reached by the `?focus=` deep link their
	// Action-required strips already hand off to — not by a per-record route, which neither has.
	contribution: (uid) => `/contributions?focus=${uid}`,
	// The notification REGISTRY, which is where a rule lives. It has no per-rule route and no
	// `?focus=` to hand one, so this lands on the page that owns the record rather than on the row
	// — which is the honest half of the link, and better than none: the chip already carries the
	// rule's NAME, so the reader knows which line to look for when they arrive.
	notification_rule: () => '/activity/notifications'
};

export function portalEntityHref(entity: ActionEntity): string | null {
	if (!entity.uid) return null;
	return ROUTES[entity.kind]?.(entity.uid) ?? null;
}
