/**
 * Test-only scaffolding for `identity-verification.dom.test.ts`.
 *
 * Runes are only available in a `.svelte`/`.svelte.ts` module — a plain `.test.ts` cannot declare
 * `$state`. One case needs to hand the mounted component a FRESH `capture` after it has already
 * rendered (a poll landing, the same way the parent page's own re-render would), and only a
 * reactive props object makes that observable.
 */
export function reactiveProps<T extends object>(initial: T): T {
	const props = $state(initial);
	return props;
}
