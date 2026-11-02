/** Stand-in so this package typechecks its `$app/forms` imports without depending on SvelteKit.
 *  The portals resolve the real `$app/forms` when they compile the component. */
import type { Action } from 'svelte/action';

export const enhance: Action<HTMLFormElement, undefined> = () => {};

/** The slice of SvelteKit's `ActionResult` this package reads. */
export type ActionResult =
	| { type: 'success' | 'failure'; status: number; data?: Record<string, unknown> }
	| { type: 'redirect'; status: number; location: string }
	| { type: 'error'; status?: number; error: unknown };

export const deserialize = (text: string): ActionResult => JSON.parse(text) as ActionResult;

export const applyAction: (result: ActionResult) => Promise<void> = async () => {};
