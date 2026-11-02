import type { HandleClientError } from '@sveltejs/kit';
import { reportUnhandled } from '@graphene/ui';

/**
 * Client-side failures were silent, and it cost a very long debugging session.
 *
 * A throw inside a Svelte effect — `each_key_duplicate` from a keyed `{#each}`, say — never reaches
 * `handleError`. It surfaces only as an unhandled promise rejection, and it aborts the surrounding
 * subtree mid-hydration: every `{#if}`/`{#each}` body is cleared and nothing refills it. The page
 * SSRs correctly, renders for one frame and then goes blank, with a clean console and nothing logged
 * anywhere. `reportUnhandled` makes that class of failure loud.
 */
reportUnhandled();

export const handleError: HandleClientError = ({ error, event, status, message }) => {
	console.error('[client error]', {
		url: event?.url?.pathname,
		status,
		error
	});
	return { message };
};
