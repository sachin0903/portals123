// What the bell does with the OUTCOME of an optimistic write — mark read, dismiss, dismiss all.
//
// The bell applies each of those to its own state first (the row greys, the badge drops, the row
// goes) and then tells the app, which posts to its proxy. The callbacks used to return `void`, so
// a refused or lost write left the screen claiming something the server never recorded — and,
// with the badge poll gone in favour of the realtime stream, nothing came along later to correct
// it. A callback may now answer with whether the write landed; a `false` (or a rejection) puts
// the bell back in step by RE-ASKING the server, the same "invalidate, don't trust a delta" seam
// the refetches already use, rather than by replaying an inverse of the optimistic change.

/** What an app's write callback may return: nothing (fire-and-forget, as before), a verdict, or a
 *  promise of either. */
export type WriteOutcome = void | boolean | Promise<void | boolean>;

/**
 * Run `resync` once `outcome` says the write did NOT land — a resolved `false`, or a rejection.
 * `undefined` (a `void` callback) is taken as "no news", so apps that never report keep the
 * behaviour they had. Never throws.
 */
export function settleWrite(outcome: WriteOutcome, resync: () => void): Promise<void> {
	return Promise.resolve(outcome).then(
		(ok) => {
			if (ok === false) resync();
		},
		() => resync()
	);
}
