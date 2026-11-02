/** Reading the outcome of a form action posted with `fetch` (not `use:enhance`).
 *
 *  THE TRAP: SvelteKit answers an action POST that negotiated JSON with **HTTP 200 even for a
 *  `fail()`** — `handle_action_json_request` builds `{ type: 'failure', status, data }` and hands it
 *  to `json(...)` with no status override (see @sveltejs/kit runtime/server/page/actions.js). So
 *  `if (!res.ok)` never fires: a 403 from a permission check looked exactly like a success, which is
 *  why the client record's archive and file-delete buttons appeared to do nothing and said nothing.
 *  The outcome lives in the deserialised body's `type`, not in the HTTP status.
 */
import type { ActionResult } from '@sveltejs/kit';

export type Outcome = { ok: boolean; error?: string };

/** Whether an action succeeded, and the message to show when it didn't. */
export function outcomeOf(result: ActionResult, fallback = 'Something went wrong.'): Outcome {
	if (result.type === 'success') return { ok: true };
	if (result.type === 'failure') {
		const data = result.data as { error?: unknown } | undefined;
		return { ok: false, error: typeof data?.error === 'string' ? data.error : fallback };
	}
	// A redirect out of an action here is the guard sending an unauthenticated caller to /login.
	if (result.type === 'redirect')
		return { ok: false, error: 'Your session has expired — sign in again.' };
	return { ok: false, error: fallback };
}
