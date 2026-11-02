/**
 * Injected navigation for the shared documents surfaces (the portal waterfall): the components
 * hardcode NO hrefs — the platform passes `/documents/{uid}`-shaped builders, the admin portal
 * will pass `/tenants/{uid}/book/...` ones. Data shapes live in `./documents.ts`/`./display.ts`;
 * these are only the link contracts plus the small cross-dialog prefill shape.
 *
 * (There is no share-link contract here any more: document share links are gone, so a document is
 * reached through the portal it was filed into or not at all.)
 */

export type DocumentsLibraryLinks = {
	/** A document's detail page, from a library row or an activity event. */
	document: (uid: string) => string;
	/** The same-origin content proxy for a document (inline preview + download). */
	documentContent: (uid: string) => string;
	/** A supporting file's download route (files have no detail page — they download). */
	fileDownload: (uid: string) => string;
	/** The record of the client a row is filed FOR — the library's Client column.
	 *
	 *  Same contract as `DocumentDetailLinks.client` below, and optional for the same reason: a
	 *  read-only mount with no client route states the name as plain text rather than offering a
	 *  dead link. Takes the contact UID, never the serial — the routes are uid-typed. */
	client?: (contactUid: string) => string;
	/** The requests hub (header button + the detail page's "Raise a request"). */
	requests: string;
	/** Where "Generate" starts (the templates library). Omit to hide the button even when the
	 *  surface is writable — generating starts from a template, which the app may not host. */
	templates?: string | null;
};

export type DocumentDetailLinks = {
	/** The documents library — the not-found fallback and the back button's no-history fallback. */
	documents: string;
	// No request links here any more: the record's Requests tab is gone, along with the "Raise a
	// request" button that carried `requests` / `requestForClient` and the per-request `request(uid)`
	// the tab's cards linked to. Requests are reached from the hub, or from a client's own record.
	// (`DocumentsLibraryLinks.requests` above is a different thing — the library kebab item.)
	/** The record of the client this document is filed FOR — the Details tab's "Filed for" row.
	 *
	 *  Injected like every other href because the route is the app's convention: the platform points
	 *  at `/clients/{id}`, the admin portal at its own book path. Absent ⇒ the client is stated as
	 *  plain text rather than linked, which is the read-only posture. */
	client?: (contactUid: string) => string;
	/** The template that generated this document (the Details tab's provenance link). */
	template: (uid: string) => string;
};
