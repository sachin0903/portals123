<script lang="ts">
	// One row of an inspector: a label and the control answering it. The shape was copy-pasted across
	// `branding/block-inspector`, `automations/action-config` and the flow-editor inspectors, each
	// re-deciding the label width; `set` marks the label and offers a clear, since "0" and "not set" are different answers and only one of them inherits.
	import type { Snippet } from 'svelte';
	import { cn } from '../../../utils.js';
	import XIcon from '@lucide/svelte/icons/x';

	let {
		label,
		for: htmlFor,
		set = false,
		hint,
		onclear,
		align = 'center',
		layout = 'row',
		class: className,
		headerEnd,
		children
	}: {
		label: string;
		/** Points the label at its control, when the control is a single focusable element. */
		for?: string;
		/** The property carries a value — lights the label and reveals the clear affordance. */
		set?: boolean;
		/** One short line under the control, for a rule that is not obvious from the label. */
		hint?: string;
		/** Absent ⇒ no clear affordance (a property that cannot meaningfully be unset). */
		onclear?: () => void;
		/** `start` for a control taller than one line — the four-up `BoxSides` grid, a stack. */
		align?: 'center' | 'start';
		/** `stack` puts the label ABOVE the control, so a long value gets the panel's full width: beside a fixed label in a 264px rail a URL has about 90px left, which is not a field anyone can read. */
		layout?: 'row' | 'stack';
		class?: string;
		/** One control on the HEADER line, `stack` only — the box's linked/split toggle, which answers the
		 *  whole property rather than any one field and so belongs beside the label, not above the grid. */
		headerEnd?: Snippet;
		children: Snippet;
	} = $props();
</script>

<!-- STACKED, THE LABEL AND THE CLEAR SHARE A HEADER LINE: the container is `flex-col`, so a third child stacked too and the × dropped below the hint, reading as a stray button. `row` is untouched. -->
{#snippet clearSlot()}
	<!-- HELD ONLY WHERE THERE IS A CLEAR. It used to be held on every row so the controls' right edges
	     lined up down a rail that mixed the two — but the style panel no longer clears anything (each
	     control says "unset" in its own words), so the slot was 20px of reserved nothing on every row. A
	     panel that genuinely mixes cleared and uncleared rows will show it, which is the honest cue. -->
	{#if onclear}
		<button
			type="button"
			aria-label={`Clear ${label}`}
			title={`Clear ${label}`}
			disabled={!set}
			onclick={onclear}
			class={cn(
				'text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex size-5 shrink-0 items-center justify-center rounded transition-opacity focus-visible:ring-2 focus-visible:outline-none',
				// Nudged down only in a ROW, against a control on the same line; on the stacked header it aligns against the label, where the same offset reads as a misalignment.
				layout === 'row' && 'mt-0.5 self-start',
				set ? 'opacity-100' : 'pointer-events-none opacity-0'
			)}
		>
			<XIcon class="size-3" />
		</button>
	{/if}
{/snippet}

<div
	class={cn(
		'flex min-w-0 py-1',
		layout === 'stack' ? 'flex-col gap-1' : 'gap-2',
		layout === 'row' && (align === 'start' ? 'items-start' : 'items-center'),
		className
	)}
>
	<!-- A data label, not the tracked-uppercase section band, and deliberately LIGHTER than the section heading above it: at `font-medium` the two weights read as one texture. -->
	{#if layout === 'stack'}
		<div class="flex min-w-0 items-center gap-2">
			<label
				for={htmlFor}
				class={cn(
					'flex min-w-0 flex-1 items-center gap-2 text-xs font-normal',
					// A SET PROPERTY IS DARKER, NOT COLOURED: as `text-brand` a panel of twenty rows lit up in accent the moment anything was styled, so the colour became texture rather than a marker.
					set ? 'text-foreground' : 'text-muted-foreground'
				)}
			>
				{label}
			</label>
			{#if headerEnd}
				{@render headerEnd()}
			{/if}
			{@render clearSlot()}
		</div>
		<div class="flex min-w-0 flex-col gap-1">
			{@render children()}
			{#if hint}
				<p class="text-muted-foreground text-[0.7rem] leading-snug">{hint}</p>
			{/if}
		</div>
	{:else}
		<label
			for={htmlFor}
			class={cn(
				'text-xs font-normal',
				// A FIXED COLUMN, so every row lines up on the same edge. 6.5rem up from 5.25rem, at which a
				// two-word label ('Corner radius') wrapped; the reference splits 137px to 150px in 320px.
				'w-[6.5rem] shrink-0',
				align === 'start' && 'pt-1.5',
				set ? 'text-foreground' : 'text-muted-foreground'
			)}
		>
			{label}
		</label>
		<div class="flex min-w-0 flex-1 flex-col gap-1">
			{@render children()}
			{#if hint}
				<p class="text-muted-foreground text-[0.7rem] leading-snug">{hint}</p>
			{/if}
		</div>
		{@render clearSlot()}
	{/if}
</div>
