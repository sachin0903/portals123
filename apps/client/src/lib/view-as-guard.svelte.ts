/**
 * Turning the API's refusal into something a person can read.
 *
 * A view-as session is read-only, and the API enforces that by refusing every write
 * (`app/view_as.py::permits`). Without this, a member of staff who clicks something the portal
 * still offers gets whatever that particular page does with an error — a red line at the top of
 * a form, or nothing at all — and has to work out that the refusal was about the session rather
 * than about the client's data.
 *
 * So one interception, in one place: any write this portal attempts that comes back refused
 * raises a dialog saying plainly what happened. The wrapper is installed only for a view-as
 * session, and only in the browser; a client's own session is left entirely alone.
 */

/** Whether the "you cannot act for the client" dialog is showing. */
let open = $state(false);

export const viewAsBlocked = {
	get open(): boolean {
		return open;
	},
	set open(value: boolean) {
		open = value;
	}
};

/** Requests that cannot be a write, so a refusal on one is about something else. */
const READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/** The status the API answers a refused write with. */
const REFUSED = 403;

/**
 * Whether this response is a refusal, in either of the two shapes this portal produces.
 *
 * A `+server.ts` proxy passes the API's status straight through, so that one is simply a 403.
 * A form action does not: SvelteKit answers `fail(403, …)` with the outcome serialised into the
 * body, and the pages read it with `deserialize`. Only the envelope is needed here — `type` and
 * `status` are plain JSON on it — so this reads that rather than pulling in devalue.
 */
async function refused(response: Response, url: string): Promise<boolean> {
	if (response.status === REFUSED) return true;
	if (!url.includes('?/')) return false;
	try {
		const body = JSON.parse(await response.text()) as { type?: string; status?: number };
		return body.type === 'failure' && body.status === REFUSED;
	} catch {
		return false;
	}
}

/**
 * Wrap `window.fetch` so a refused write raises the dialog. Returns the undo, for the effect
 * that installed it.
 *
 * Only this portal's own routes are watched: they are the ones that proxy to the API with the
 * session's token, and a 403 from somewhere else is not ours to explain. The response is passed
 * through untouched, so whatever the calling page does with a failure still happens — the dialog
 * is an addition to the existing handling, never a replacement for it.
 */
export function guardWrites(): () => void {
	const original = window.fetch;
	window.fetch = async (input, init) => {
		const response = await original(input, init);
		const method = (
			init?.method ?? (input instanceof Request ? input.method : 'GET')
		).toUpperCase();
		if (!READ_METHODS.has(method) && sameOrigin(input)) {
			// Read from a clone: the caller has not touched the body yet and still needs it.
			if (await refused(response.clone(), urlOf(input))) open = true;
		}
		return response;
	};
	return () => {
		window.fetch = original;
	};
}

function urlOf(input: RequestInfo | URL): string {
	return input instanceof Request ? input.url : String(input);
}

function sameOrigin(input: RequestInfo | URL): boolean {
	const url = urlOf(input);
	// A relative path is this portal by definition; an absolute one has to name it.
	if (!/^https?:\/\//i.test(url)) return true;
	try {
		return new URL(url).origin === window.location.origin;
	} catch {
		return false;
	}
}
