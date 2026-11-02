/**
 * One feedback path for every mutation on the shared book surfaces: success toasts, failures toast
 * the action's own error, and an optional pending setter drives the submit button's disabled/label
 * state. An action that half-succeeded returns a `warning` and gets a warning toast — never a
 * success one it hasn't earned.
 *
 * Lives in pkgs/ui because the forms that use it moved here (clients listing + client record), but
 * this package cannot import `$app/forms` — SvelteKit's virtuals only exist inside an app. The app
 * therefore INJECTS its own `enhance` as a prop, typed structurally below. The structural shapes
 * are deliberately narrower than SvelteKit's (a real `SubmitFunction` input carries more fields
 * than `formData` + `cancel`); TypeScript's contravariance makes the real `enhance` assignable to
 * `Enhance`, and a `withToast` handler assignable to a real `SubmitFunction`.
 */
import { toast } from 'svelte-sonner';

/** The slice of SvelteKit's `ActionResult` these handlers read. */
export type EnhanceOutcome = {
	type: 'success' | 'failure' | 'redirect' | 'error';
	status?: number;
	data?: Record<string, unknown>;
};

export type EnhanceUpdate = (opts?: { reset?: boolean; invalidateAll?: boolean }) => Promise<void>;

/** What a submit handler may return: a callback that runs when the action result arrives. */
export type EnhanceCallback = (opts: {
	result: EnhanceOutcome;
	update: EnhanceUpdate;
}) => void | Promise<void>;

/** The submit-function shape (structural subset of SvelteKit's `SubmitFunction`). */
export type EnhanceSubmit = (input: {
	formData: FormData;
	cancel: () => void;
}) => void | EnhanceCallback | Promise<void | EnhanceCallback>;

/** The `enhance` action itself, as injected by the app (`import { enhance } from '$app/forms'`). */
export type Enhance = (form: HTMLFormElement, submit?: EnhanceSubmit) => { destroy(): void } | void;

export function errorOf(result: EnhanceOutcome): string {
	return result.type === 'failure'
		? String(result.data?.error ?? 'Something went wrong.')
		: 'Something went wrong.';
}

/**
 * `success` may be a function of the action's own result: `use:enhance` evaluates its argument ONCE,
 * at mount (it returns only `{destroy}`), so a message built from page state — e.g.
 * `c.enabled ? 'deactivated' : 'reactivated'` — is frozen at the state before the first click and
 * then lies. Anything that changes with the outcome must be read from `result.data`
 * (see ./messages.ts).
 */
export function withToast(
	success: string | ((data: Record<string, unknown> | undefined) => string),
	opts: {
		pending?: (v: boolean) => void;
		onSuccess?: (data?: Record<string, unknown>) => void;
		reset?: boolean;
	} = {}
): EnhanceSubmit {
	return () => {
		opts.pending?.(true);
		return async ({ result, update }) => {
			opts.pending?.(false);
			if (result.type === 'success') {
				const data = result.data;
				const warning = (data as { warning?: string } | undefined)?.warning;
				if (warning) toast.warning(warning);
				else toast.success(typeof success === 'function' ? success(data) : success);
				// Refresh FIRST. `onSuccess` typically closes an editor, and some registers
				// (Compliance's source of wealth) are hidden while that editor is open — closing
				// before `invalidateAll` reseeds them from the STALE contact, so a successful save
				// reads as a no-op until something else happens to reload the page.
				await update({ reset: opts.reset ?? true });
				opts.onSuccess?.(data);
			} else {
				toast.error(errorOf(result));
				await update({ reset: opts.reset ?? true });
			}
		};
	};
}
