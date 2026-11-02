<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '../../../utils.js';

	/**
	 * The subheading that separates bands within a page ("Applicants", "Needs attention"). Several
	 * screens had invented this by hand at slightly different sizes; this is
	 * the single version, with an optional count and right-aligned actions.
	 *
	 * Sentence case, not the tracked small caps it used to be: uppercasing a heading of real words
	 * costs legibility (the word shapes go) to buy a "this is a label" cue, and the cue is bought
	 * more cheaply here with weight and ink — the title is `--foreground` semibold against the muted
	 * body beneath it. The count keeps the muted tone so the heading still reads as one word plus a
	 * tally rather than two competing figures.
	 */
	let {
		title,
		count,
		actions,
		class: className
	}: {
		title: string;
		/** Rendered as a muted tally after the title. */
		count?: number | string;
		actions?: Snippet;
		class?: string;
	} = $props();
</script>

<div class={cn('mb-2.5 flex items-center justify-between gap-3', className)}>
	<h2 class="text-[0.8125rem] leading-5 font-semibold">
		{title}{#if count !== undefined}<span class="text-muted-foreground/60 ml-1.5 tabular-nums"
				>{count}</span
			>{/if}
	</h2>
	{#if actions}
		<div class="flex shrink-0 items-center gap-1.5">{@render actions()}</div>
	{/if}
</div>
