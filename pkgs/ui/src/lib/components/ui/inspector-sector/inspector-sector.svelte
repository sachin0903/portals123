<script lang="ts">
	// One band of an inspector accordion. The style rail had FIVE hand-repeated copies of this header (the
	// style panel's sector, plus Visibility, Field, Conditional text and Link/Image in `email-editor.svelte`) and they had already drifted in what the dot meant — the `FIELD_CLASS` argument, one level above `PropertyRow`. `Collapsible` owns the button, aria and keyboard.
	import type { Snippet } from 'svelte';
	import * as Collapsible from '../collapsible/index.js';
	import { cn } from '../../../utils.js';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';

	let {
		label,
		open = $bindable(true),
		set = false,
		mark,
		class: className,
		onOpenChange,
		children
	}: {
		label: string;
		/** Bindable, so a caller can drive it (a new selection opening its own inspector). */
		open?: boolean;
		/** The section carries a value — a condition, a chosen format, a set style property. */
		set?: boolean;
		/** A symbol in place of the dot, for a section whose FACT is worth naming rather than merely
		 *  counting: a conditional block is marked here and nowhere on the canvas, because every
		 *  on-element treatment was eclipsed by the element itself. Shown only when `set`. */
		mark?: Snippet;
		class?: string;
		onOpenChange?: (open: boolean) => void;
		children: Snippet;
	} = $props();
</script>

<Collapsible.Root
	bind:open
	onOpenChange={(o) => {
		onOpenChange?.(o);
	}}
>
	<Collapsible.Trigger
		class={cn(
			// SENTENCE CASE, not the tracked-uppercase section band: six deep in a 264px rail that read as a
			// wall of shouting. NO DIVIDER either — the old full-width `border-b` drew a table of rules, so `pt-3` air is the whole separation (`first:pt-2` keeps the panel top flush).
			'group/sector text-foreground hover:text-foreground flex w-full cursor-pointer items-center gap-1.5 px-2 pt-3 pb-1 text-left text-xs font-bold transition-colors first:pt-2',
			className
		)}
	>
		<!-- The caret stays MUTED against foreground ink: at one weight the mechanism competed with the label. -->
		<ChevronDownIcon
			class="text-muted-foreground size-3.5 shrink-0 transition-transform group-data-[state=closed]/sector:-rotate-90"
		/>
		{label}
		{#if set}
			<!-- The dot says the section CARRIES A VALUE, so it must not hide while collapsed — which is exactly when the values it stands for are out of sight. A `mark` replaces it where the fact deserves naming. -->
			<span class="text-muted-foreground ml-auto flex shrink-0 items-center">
				{#if mark}
					{@render mark()}
				{:else}
					<span class="bg-muted-foreground/60 size-1.5 rounded-full"></span>
				{/if}
			</span>
		{/if}
	</Collapsible.Trigger>
	<Collapsible.Content>
		{@render children()}
	</Collapsible.Content>
</Collapsible.Root>
