// The active-contact query parameter, read off a same-origin proxy's own URL.
//
// A login linked to more than one `crm.contacts` row picks ONE active client in the nav switcher,
// and every read on this portal has to agree about which one that is: the root layout resolves it
// (`+layout.server.ts`), puts it in the endpoint URLs it hands the bell, and the proxies forward it
// to the API. Without the forward the API falls back to RLS alone, which is EVERY linked contact —
// so the bell's badge counted one client while the list beside it showed them all.
//
// Extracted because three proxies need the same rule and the failure is silent in both directions:
// a dropped parameter merges other clients' rows in, and a malformed one 422s the API, which the
// proxies dress as "leave the rows alone" — a bell that quietly stops updating.

/**
 * The validated `contact_id` on `url`, or `null` when there is none to forward.
 *
 * Integer-checked rather than passed through: the API types this parameter `int`, so a junk value
 * is a 422 the caller reads as a failed refetch. It is NOT ownership-checked — that is the API's
 * job (the filter is ANDed under RLS, so an id that is not the caller's returns an empty page), and
 * the layout has already validated the value against `GET /crm/contacts/mine` before it ever
 * reaches the browser.
 */
export function activeContactId(url: URL): number | null {
	return parseContactId(url.searchParams.get('contact_id'));
}

/**
 * A `contact_id` from ANY carrier — a query string, a JSON body's `contactId`, a multipart form
 * field, the active-contact cookie — as a positive integer, or `null`.
 *
 * The proxies used to splice the raw value into the API URL (`?contact_id=${contactId}`), so a
 * string body value like `1&scope=all` or `1#` rewrote the query the API saw. Everything that
 * forwards the parameter goes through here so the only thing that can reach the API is digits.
 */
export function parseContactId(raw: unknown): number | null {
	if (typeof raw === 'number') return Number.isInteger(raw) && raw > 0 ? raw : null;
	if (typeof raw !== 'string') return null;
	const value = raw.trim();
	if (value === '' || !/^\d{1,18}$/.test(value)) return null;
	const id = Number(value);
	return Number.isSafeInteger(id) && id > 0 ? id : null;
}

/** `parseContactId` as a leading query string: `?contact_id=7`, or `''` when there is none. */
export function contactIdQuery(raw: unknown): string {
	const id = parseContactId(raw);
	return id === null ? '' : `?contact_id=${id}`;
}

/** `activeContactId` as a query-string fragment: `contact_id=7`, or `''` when there is none. */
export function activeContactParam(url: URL): string {
	const id = activeContactId(url);
	return id === null ? '' : `contact_id=${id}`;
}
