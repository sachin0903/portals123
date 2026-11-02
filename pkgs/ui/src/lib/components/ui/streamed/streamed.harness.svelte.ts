/**
 * Test-only scaffolding for `streamed.dom.test.ts`.
 *
 * Runes are only available in a `.svelte`/`.svelte.ts` module — a plain `.test.ts` cannot declare
 * `$state` or run an `$effect`. That is the whole of this file: the rules under test (what happens to
 * the answer on screen when a NEW promise arrives) can only be observed by handing the mounted
 * component a new promise, and whether it flags that answer as stale can only be read from a
 * snippet that updates its node in place.
 */
import { createRawSnippet, type Snippet } from 'svelte';

export function reactiveProps<T extends object>(initial: T): T {
	const props = $state(initial);
	return props;
}

/**
 * A content snippet that renders ONCE and then patches the same `<p>`: the identity assertions in
 * the dom test need to tell "updated in place" from "remounted", and the `data-stale` attribute is
 * how the test reads `Streamed`'s second argument.
 */
export function bookSnippet<T extends { label: string }>(): Snippet<[T, boolean]> {
	return createRawSnippet((book: () => T, stale: () => boolean) => ({
		render: () => `<p data-testid="book" data-stale="${stale()}">${book().label}</p>`,
		setup: (el) => {
			$effect(() => {
				el.textContent = book().label;
				(el as HTMLElement).dataset.stale = String(stale());
			});
		}
	})) as unknown as Snippet<[T, boolean]>;
}
