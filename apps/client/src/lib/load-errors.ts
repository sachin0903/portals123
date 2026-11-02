/**
 * What to say when a slice read failed — the pure half, importable from a component.
 *
 * Its own module rather than a corner of `page-data.ts` because of the boundary: `page-data.ts`
 * reaches `$lib/api.ts`, which reads `$env/dynamic/private`, so anything a Svelte component
 * imports from it by VALUE pulls a server-only module into the browser bundle and the build
 * refuses. `import type` is erased and was always fine; a function is not. Nothing here touches
 * env, fetch or the API — it turns a list of failures into a sentence.
 */

/** One slice read the API refused or never answered. `status` is 0 when the fetch itself threw
 * (DNS, connection refused, TLS) — distinct from an HTTP status, and usually the more telling of
 * the two: it means the portal could not reach the API at all. */
export interface SliceFailure {
	slice: string;
	status: number;
}

/** Plain-language cause for a slice failure, for the banner.
 *
 * Every slice fetch is best-effort, so without this a refused API and a client who owns nothing
 * render identically — the failure mode that made an empty portal look like correct behaviour.
 * The statuses that occur here (401, 403, 5xx, 0 for an unreachable API) each say something
 * about the DEPLOYMENT rather than about the client's holdings, which is why the client is told
 * only that something failed and to retry: the diagnosis is for the operator's logs, not for a
 * banner. The `failures` list is kept, not collapsed to a boolean, so a caller that wants to log
 * or later differentiate has the slice names and statuses to hand.
 */
export function loadErrorSummary(failures: SliceFailure[]): string | null {
	if (failures.length === 0) return null;
	return 'Some of your information could not be loaded. Please try again shortly.';
}
