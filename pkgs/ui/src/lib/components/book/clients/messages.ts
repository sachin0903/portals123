/** Outcome messages for the client record's forms.
 *
 *  These are pure so they can be asserted: `use:enhance` evaluates its submit function ONCE, at
 *  mount (it returns only `{destroy}` — there is no `update` hook to re-run it), so any message
 *  built from page state is frozen at the state before the first submit. Deactivate then reactivate
 *  and a message read from `contact.enabled` still said "Client deactivated". The message therefore
 *  has to be derived from what the ACTION returned.
 */

/** Toast text for `?/setEnabled`, from the action's own result (`{ enabled }`). */
export function setEnabledMessage(data: Record<string, unknown> | undefined): string {
	return data?.enabled === true ? 'Client reactivated' : 'Client deactivated';
}
