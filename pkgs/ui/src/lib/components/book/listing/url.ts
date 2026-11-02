/**
 * Building the next URL for a listing whose query lives in the address bar.
 *
 * Every listing that narrows server-side has to put its question somewhere the LOADER can read it,
 * and that place is the URL: it survives a reload, it can be linked, and it is the only state a
 * SvelteKit `load` can see. The mechanics are the same on every one of them — set a param, or drop
 * it when the value is empty — and they were being written out per page, which is how one of them
 * ends up clearing a param the others keep.
 *
 * Pure on purpose. Navigating needs `goto`, which is a SvelteKit virtual that this package cannot
 * import; the app passes the result to `goto`. That split also makes the interesting half — which
 * params survive, which are dropped, what an empty string means — testable without a browser.
 */

/** A change to a listing's query params. `null`, `undefined` and `''` all REMOVE the param. */
export type UrlPatch = Record<string, string | number | null | undefined>;

/**
 * The current URL with `patch` applied, as a new `URL` — the original is never mutated.
 *
 * An empty value REMOVES its param rather than setting it blank. `?q=` and no `q` mean the same
 * thing to every loader here ("no search"), and leaving the empty one behind makes two URLs for one
 * state: the back button then walks through a param that changes nothing, and a "clear" that leaves
 * `?q=` looks like it did not work.
 *
 * Params NOT named in the patch are kept. That is what lets a search change without dropping the
 * filters beside it — the bug this replaces, where each page rebuilt the query string from the one
 * control it owned and silently reset the others.
 */
export function nextUrl(current: URL, patch: UrlPatch): URL {
	const url = new URL(current);
	for (const [key, value] of Object.entries(patch)) {
		// The NUMBER zero is a default (offset 0 is the first page) and is dropped; the STRING '0'
		// is a real value somebody typed and is kept. Testing the type rather than the rendered
		// text is what keeps those apart — `String(0)` and `'0'` are the same three characters.
		const empty =
			value === null || value === undefined || value === 0 || String(value).trim() === '';
		if (empty) url.searchParams.delete(key);
		else url.searchParams.set(key, String(value).trim());
	}
	return url;
}

/**
 * The same, but also returning to the first page.
 *
 * Changing a search or a filter while on page 3 has to reset the offset: the new result set is a
 * different list, and page 3 of it is usually empty — which reads as "no matches" for a search that
 * matched plenty. Every listing that pages wants this, and forgetting it is invisible until someone
 * searches from a later page.
 */
export function nextUrlFromStart(current: URL, patch: UrlPatch): URL {
	return nextUrl(current, { ...patch, offset: null, page: null });
}
