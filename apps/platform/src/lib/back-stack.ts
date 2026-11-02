/**
 * Where "Back" goes — the trail of pages you can return to, in the order you actually visited them.
 *
 * The bug this replaces: every back link was the route's PARENT, declared at the call site.
 * `task-detail.svelte` says `back={{ href: links.tasks(), label: 'Tasks' }}`, so a task opened from
 * the dashboard's action queue offered "Tasks" — a page the user had never been on. A task is
 * reachable from the dashboard, the tasks listing, global search, the audit log and two chat rails,
 * and the parent is the right answer for exactly one of them.
 *
 * Pure on purpose — no runes, no `$app/*`, no browser globals. The state lives in
 * `back-stack.svelte.ts`; everything that could be got wrong lives here, where it is testable
 * without a router.
 */

/** One page you can go back to. `label` is a bare name ("Tasks", "Dashboard", "Mr Max Oakden"); the
 *  control that renders it supplies the wording. */
export type BackEntry = { href: string; label: string };

/**
 * A page's IDENTITY — the pathname, with the query and hash dropped.
 *
 * Every listing in this app drives its filters, tabs and paging with `goto('?' + qs)`, which fires
 * the navigation lifecycle exactly like a real page change (only `replaceState` from
 * `$app/navigation` — shallow routing, which nothing here uses — would not). Comparing full hrefs
 * would therefore push a stack entry for each filter tick, and Back would walk you through your own
 * filter history one click at a time.
 *
 * The STORED href keeps its query, so going back restores the filters and page number you left. It
 * is only the comparison that drops it.
 */
export const pageKey = (href: string): string => href.split(/[?#]/)[0];

/** How far back the trail remembers. Ten is past the point anyone reconstructs their own path; the
 *  cap exists so a long session cannot grow the array without bound. */
export const CAP = 10;

/**
 * The stack after arriving at `arriving`, having left `leaving` (null ⇒ nothing worth recording).
 *
 * The stack holds pages you can go BACK to, so it never contains the page you are on.
 *
 * Truncation keys on the DESTINATION, not on the page being left, and that single choice is the
 * whole loop rule: x → y → z leaves `[x, y]`; going z → y finds y already in the stack and cuts to
 * `[x]`, so Back from y goes to x. Push-then-check would give `[x, y, z]` and Back from y would
 * return to z — the loop that makes `history.back()` useless here, and the reason
 * `back-button.svelte` has always refused to call it.
 *
 * The browser's own back and forward buttons need no special case: back lands on a page that is in
 * the stack (truncate), forward lands on one that is not (push). Both come out right, and
 * special-casing `popstate` to "truncate only" would get forward wrong.
 */
export function advance(
	stack: BackEntry[],
	leaving: BackEntry | null,
	arriving: string,
	cap = CAP
): BackEntry[] {
	const key = pageKey(arriving);
	const at = stack.findIndex((entry) => pageKey(entry.href) === key);
	if (at !== -1) return stack.slice(0, at);
	if (!leaving) return stack;
	// Same page, different query: a filter change, not a journey.
	if (pageKey(leaving.href) === key) return stack;
	const next = [...stack, leaving];
	return next.length > cap ? next.slice(next.length - cap) : next;
}

/** The page Back returns to, or null when nothing has been visited yet. */
export const top = (stack: BackEntry[]): BackEntry | null => stack[stack.length - 1] ?? null;

/**
 * Where Back goes: what you actually came from, else the origin the LINK declared, else nothing.
 *
 * `?from=`/`?fromLabel=` predates the stack — a handful of pages are linked with their origin spelt
 * into the URL (a form opened from a template's overview). It survives as the second layer because
 * it is the one answer that outlives a reload, and it keeps every such link already in the wild
 * working. Resolving BOTH layers here, rather than letting each control read the query for itself,
 * is what stops the topbar and the page-level control disagreeing — which would be this same bug in
 * a second costume.
 *
 * Only same-origin relative paths are honoured: a crafted `?from=https://evil.example` must not
 * become an open redirect. Guard carried over verbatim from `back-button.svelte`.
 */
export function resolveBack(stackTop: BackEntry | null, url: URL): BackEntry | null {
	if (stackTop) return stackTop;
	const from = url.searchParams.get('from');
	if (!from || !/^\/(?!\/)/.test(from)) return null;
	return { href: from, label: url.searchParams.get('fromLabel') || 'previous' };
}
