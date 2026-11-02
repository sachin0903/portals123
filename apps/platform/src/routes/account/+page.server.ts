import { fail } from '@sveltejs/kit';
import type { Actions } from './$types';

/** Copy shared by the action and the page, so the refusal and the explanation cannot drift. */
export const _PROFILE_READ_ONLY_MESSAGE =
	'Your name and email address come from your firm’s records. Ask an administrator to change them.';

export const actions: Actions = {
	/**
	 * There is no endpoint that persists staff name/email (`/me` is read-only), so this action
	 * CANNOT save. It used to validate the fields and return `{ saved: true }`, and the page then
	 * rendered "Your details have been saved." — telling the user their details had changed when
	 * nothing had been written anywhere. Fail closed and say so instead; the page renders the same
	 * message and read-only fields, so this only fires for a hand-crafted or stale POST.
	 */
	default: async () => fail(503, { error: _PROFILE_READ_ONLY_MESSAGE })
};
