/**
 * The back trail for this browser tab. The algorithm lives in `./back-stack` — this is only the
 * state around it.
 *
 * Module-level rather than a context, for the reason `./reading.svelte.ts` gives: the writer is the
 * root layout and the readers are controls several layers below it, with no shared state to thread
 * through, and one browser tab is exactly the right scope for one trail.
 *
 * SSR: the only writers are `beforeNavigate`/`afterNavigate`, which never run on the server, so the
 * stack is always empty there and the server renders the fallback. `commitNavigation` still guards,
 * because module state on the server is per-PROCESS and a stray write would leak one user's trail
 * into another user's page. Nothing outside this module may write it.
 *
 * Deliberately not persisted to `sessionStorage`: it would survive a full page load, which is
 * precisely when the trail is no longer true, and buy a class of hydration mismatch for it.
 */

import { advance, top, type BackEntry } from './back-stack';

let stack = $state<BackEntry[]>([]);

/** Where Back goes, or null when nothing has been visited in this tab. Reactive — read it inside a
 *  `$derived` or a template. */
export function backTop(): BackEntry | null {
	return top(stack);
}

/** Commit a navigation that actually happened. Called ONLY from the root layout's `afterNavigate`. */
export function commitNavigation(leaving: BackEntry | null, arriving: string): void {
	if (typeof window === 'undefined') return;
	stack = advance(stack, leaving, arriving);
}

/** Forget everything. A back target from before a session boundary is at best confusing and at
 *  worst a 403, so signing in, signing out and landing on any auth screen all clear it. */
export function clearBackStack(): void {
	stack = [];
}
