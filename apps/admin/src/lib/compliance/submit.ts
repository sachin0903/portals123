import { toast } from '@graphene/ui';
import type { SubmitFunction } from '@sveltejs/kit';

/**
 * A form's `use:enhance`: track pending, toast the outcome, re-run the load.
 *
 * `update()` is SvelteKit's own result handling, so a redirect the action returns (a first screen
 * moving the page to the client's lasting key) is followed rather than swallowed.
 */
export function submit(
	label: string,
	busy: (pending: boolean) => void,
	done?: () => void
): SubmitFunction {
	return () => {
		busy(true);
		return async ({ result, update }) => {
			busy(false);
			if (result.type === 'success' || result.type === 'redirect') {
				toast.success(label);
				done?.();
			} else if (result.type === 'failure') {
				toast.error(String(result.data?.error ?? 'Something went wrong.'));
			}
			await update();
		};
	};
}
