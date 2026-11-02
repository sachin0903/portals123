/**
 * Event-stream entity → an admin record page, scoped to the firm the event belongs to.
 *
 * TWO FAMILIES, because admin has two ways into a firm's records and they are not interchangeable:
 *
 *   * OPERATE (`/tenants/{uid}/operate/…`) — acting on the firm's behalf. It has a page for every
 *     kind the trail resolves, so it routes the most.
 *   * BOOK (`/tenants/{uid}/book/…`) — the read-only oversight view. Only clients and accounts have
 *     a record page there; a document or a task would 404, so neither is offered.
 *
 * ADDRESSED BY UID, never the serial the stream recorded — that serial is history, and these routes
 * are uid-typed. An entity the API could not resolve carries no uid, so there is nothing to link.
 *
 * The detail pane picks its "Open …" button from the first entity these can route, so a kind
 * missing here is a button that does not appear.
 */
import type { ActionEntity } from '@graphene/ui';

const OPERATE: Record<string, (tenantUid: string, uid: string) => string> = {
	contact: (t, uid) => `/tenants/${t}/operate/clients/${uid}`,
	account: (t, uid) => `/tenants/${t}/operate/accounts/${uid}`,
	document: (t, uid) => `/tenants/${t}/operate/documents/${uid}`,
	task: (t, uid) => `/tenants/${t}/operate/tasks/${uid}`,
	template: (t, uid) => `/tenants/${t}/operate/templates/${uid}`,
	form: (t, uid) => `/tenants/${t}/operate/forms/${uid}`,
	flow: (t, uid) => `/tenants/${t}/operate/flows/${uid}`,
	// Added with the September 2026 event-content review, which gave these kinds resolvable names.
	// Only the kinds operate has a record page for — `contributions` and `transfers` are listings
	// with no per-record route here, so their chips carry the name and no link.
	journey: (t, uid) => `/tenants/${t}/operate/journeys/${uid}`,
	content: (t, uid) => `/tenants/${t}/operate/content/${uid}`,
	automation: (t, uid) => `/tenants/${t}/operate/automations/${uid}`,
	dataset: (t, uid) => `/tenants/${t}/operate/datasets/${uid}`,
	document_request: (t, uid) => `/tenants/${t}/operate/documents/requests/${uid}`
};

const BOOK: Record<string, (tenantUid: string, uid: string) => string> = {
	contact: (t, uid) => `/tenants/${t}/book/clients/${uid}`,
	account: (t, uid) => `/tenants/${t}/book/accounts/${uid}`
};

export function operateEntityHref(tenantUid: string, entity: ActionEntity): string | null {
	if (!entity.uid) return null;
	return OPERATE[entity.kind]?.(tenantUid, entity.uid) ?? null;
}

export function bookEntityHref(tenantUid: string, entity: ActionEntity): string | null {
	if (!entity.uid) return null;
	return BOOK[entity.kind]?.(tenantUid, entity.uid) ?? null;
}
