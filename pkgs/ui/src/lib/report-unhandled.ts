/**
 * Make silent client-side failures loud.
 *
 * A throw inside a Svelte effect does NOT reach SvelteKit's `handleError`. It arrives as an unhandled
 * promise rejection, and Svelte aborts the surrounding subtree mid-hydration: the bodies of every
 * `{#if}`/`{#each}` are cleared and nothing refills them. The observable result is a page that SSRs
 * correctly, renders for a single frame, then goes blank — with no console error, nothing in the dev
 * server log, and nothing reaching `handleError`.
 *
 * That is exactly how `each_key_duplicate` in the shared topbar breadcrumb (keyed on a label that
 * legitimately repeats) killed hydration across whole portals: every popover and select inert, forms
 * only appearing after a reload, the theme toggle dead. Diagnosing it required injecting a listener
 * by hand, so install one permanently.
 *
 * Idempotent — safe to call from more than one entry point.
 */
let installed = false;

export function reportUnhandled(): void {
	if (installed || typeof window === 'undefined') return;
	installed = true;

	window.addEventListener('unhandledrejection', (event) => {
		const reason = event.reason;
		console.error(
			'[unhandled rejection]',
			reason instanceof Error ? (reason.stack ?? reason.message) : reason
		);
	});
}
