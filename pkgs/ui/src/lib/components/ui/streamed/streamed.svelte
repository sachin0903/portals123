<script lang="ts" generics="T">
	import { untrack, type Snippet } from 'svelte';
	import { LoadErrorBanner } from '../load-error-banner/index.js';

	/**
	 * Renders streamed page data — a placeholder until the answer arrives, then the content.
	 *
	 * Why it exists: a SvelteKit `load` blocks the whole navigation, so a page whose loader talks to
	 * the API and the lake shows nothing at all for the best part of a second after the click. A
	 * loader that returns a PROMISE instead lets the page render immediately and the rows arrive
	 * behind them.
	 *
	 * Why it is not just `{#await}`: these listings are server-driven, so every filter keystroke and
	 * every page cut is a fresh navigation with a fresh promise, and the two want opposite things.
	 *
	 * - A PAGE CUT (or a sort, or a filter pick) asks for different rows. Showing the placeholder
	 *   while they arrive is the honest answer: the old page is not what was asked for, and a table
	 *   that sits unchanged for two seconds after the click reads as a pager that does not work. So
	 *   a new promise swaps the content back to `pending` — the same skeleton the first load showed.
	 * - A KEYSTROKE in the search box must NOT do that. Unmounting the listing takes the search box
	 *   with it, and the focus and caret in it; the reader is typing into a field that keeps
	 *   vanishing. So while a text field has focus the last answer is HELD on screen and the rows
	 *   are replaced in place when the next answer lands (the shell's progress bar says work is
	 *   happening). The content snippet is rendered in one fixed position throughout, so the listing
	 *   inside it is updated, never remounted.
	 *
	 * "Typing" is read from `document.activeElement` at the moment the new promise arrives — a text
	 * input, a textarea or a contenteditable. Nothing else distinguishes the two navigations: both
	 * change the URL's search params, both hand this component a new promise. A pager click has
	 * moved focus to the link (or to the body, in Safari), so it swaps; a debounced search `goto`
	 * runs with `keepFocus`, so it holds.
	 *
	 * `keep` makes the hold unconditional, for a surface where most of the content does not change
	 * with the query — the dashboard's KPI tiles around its paged table. The content snippet's second
	 * argument, `stale`, is true while a held answer is being replaced, so such a surface can show
	 * the part that IS changing as loading (`DataTable`'s `loading` draws skeleton rows) without the
	 * rest of the page going grey. It is false during the typing hold, so a search does not flicker.
	 *
	 * A rejection is only shown when there is nothing to show instead — a stale table beats an error
	 * banner where the loader's own `loadError` flag is the normal way a failed fetch is reported.
	 */
	let {
		data,
		pending,
		children,
		failed,
		onretry,
		keep = false
	}: {
		/** The loader's streamed value. A plain (already-resolved) value is rendered immediately,
		 *  including during SSR, so a page that stops streaming needs no change here. */
		data: Promise<T> | T;
		/** The placeholder — a `ListingSkeleton`, or whatever the page's own shape is. */
		pending: Snippet;
		/** The content, given the resolved value and whether it is being held while a newer answer is
		 *  fetched. Declare it explicitly to receive them: `{#snippet children(book, stale)}…{/snippet}`. */
		children: Snippet<[T, boolean]>;
		/** Replaces the default retry banner when the promise rejects with nothing on screen yet. */
		failed?: Snippet;
		/** Re-runs the page's load behind the default banner's Retry (e.g. `invalidateAll`). */
		onretry?: () => void;
		/** Always hold the last answer while the next is fetched, and say so through `stale`, instead
		 *  of swapping back to `pending`. For content that mostly survives the query change. */
		keep?: boolean;
	} = $props();

	// Boxed, so `undefined` and `null` are values a loader may legitimately stream rather than
	// "nothing yet". Seeded synchronously for a non-promise, which is the only case SSR can render.
	let latest = $state<{ value: T } | null>(
		untrack(() => (data instanceof Promise ? null : { value: data as T }))
	);
	let broke = $state(false);
	/** A newer promise is in flight and the answer on screen is the previous one. */
	let stale = $state(false);

	const TEXTLESS_INPUTS = new Set([
		'button',
		'checkbox',
		'color',
		'file',
		'hidden',
		'image',
		'radio',
		'range',
		'reset',
		'submit'
	]);

	/** Is the reader typing? The reason to hold the current answer rather than swap it out. */
	function typing(): boolean {
		if (typeof document === 'undefined') return false;
		const el = document.activeElement;
		if (!(el instanceof HTMLElement)) return false;
		if (el.isContentEditable || el instanceof HTMLTextAreaElement) return true;
		return el instanceof HTMLInputElement && !TEXTLESS_INPUTS.has(el.type);
	}

	$effect(() => {
		const source = data;
		if (!(source instanceof Promise)) {
			latest = { value: source };
			stale = false;
			broke = false;
			return;
		}
		let live = true;
		untrack(() => {
			// A retry after a rejection shows the placeholder again, not the banner it is retrying.
			broke = false;
			if (latest === null) return;
			if (keep) stale = !typing();
			else if (!typing()) latest = null; // back to the placeholder — a different page was asked for
		});
		void source.then(
			(value) => {
				if (!live) return;
				latest = { value };
				stale = false;
				broke = false;
			},
			() => {
				if (live) broke = true;
			}
		);
		// A navigation that supersedes this one must not have its answer overwritten by the older
		// promise settling late.
		return () => {
			live = false;
		};
	});
</script>

{#if latest}
	{@render children(latest.value, stale)}
{:else if broke}
	{#if failed}
		{@render failed()}
	{:else}
		<LoadErrorBanner onRetry={onretry} />
	{/if}
{:else}
	{@render pending()}
{/if}
