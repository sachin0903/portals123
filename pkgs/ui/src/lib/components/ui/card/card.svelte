<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn, type WithElementRef } from '../../../utils.js';

	let {
		ref = $bindable(null),
		class: className,
		children,
		size = 'default',
		glass = false,
		elevation = 'raised',
		...restProps
	}: WithElementRef<HTMLAttributes<HTMLDivElement>> & {
		size?: 'default' | 'sm';
		/** Render as a liquid-glass panel (translucent, refracts the backdrop). */
		glass?: boolean;
		/**
		 * Depth step (see --elev-* in theme.css). `flat` for cards inside another surface,
		 * `raised` for the default page card, `floating` for the one card that must dominate.
		 */
		elevation?: 'flat' | 'raised' | 'floating';
	} = $props();

	// Depth reads the finish-axis tokens (default: the same --elev-1/--elev-3 as before) so the
	// ledger finish can flatten every card at once without touching call sites.
	const ELEVATION = {
		flat: '',
		raised: 'shadow-[var(--surface-card-shadow)]',
		floating: 'shadow-[var(--surface-card-shadow-floating)]'
	} as const;
</script>

<div
	bind:this={ref}
	data-slot="card"
	data-size={size}
	class={cn(
		// `rounded-xl` = calc(var(--radius) + 4px), which is 1rem at the 0.75rem default — pixel-identical
		// to the fixed 2xl step this replaces, but it TRACKS --radius, so the sharp surface preset (and a
		// firm branding its radius) actually reaches the product's most common surface.
		// Background + border moved off this class list into theme.css's `[data-slot='card']`
		// components-layer rule, which reads the finish-axis tokens (--surface-card*); the defaults
		// equal the old `bg-card border border-border/70`, and caller utility classes still win
		// (utilities layer beats components). The glass branch's `glass-panel` utility re-covers
		// background/border/shadow wholesale, exactly as before.
		//
		// `overflow-visible`, not `overflow-hidden`: Combobox and AddressLookup render their
		// suggestion list as an absolutely positioned box INSIDE the field (they cannot portal —
		// a Popover lands outside a Sheet's focus trap and never receives a click). A clip on the
		// card crops that list to the panel, which reads as a broken dropdown. Images that need
		// the radius clip themselves (`*:[img:…]` below). It also squares the drop shadow —
		// Chromium clips `box-shadow` to the border box before the radius, so a listing that
		// puts `overflow-hidden` here gets a rounded sheet with a sharp-cornered shadow.
		// Scroll clipping belongs on CardContent. Staff chrome re-asserts overflow:visible.
		//
		// `focus-within:z-[1]` is the other half of that. `overflow-visible` lets the list escape
		// the panel, but a later sibling still paints on top — `glass-panel`'s backdrop-filter
		// (and any explicit z-index) makes each card its own stacking context, so the field's
		// own `z-50` cannot reach past the next card. Lifting the card that holds focus / an
		// open listbox is what actually puts the list in front. `z-[1]` is enough to beat a
		// sibling at auto; it stays below the sticky topbar / sidebar (`z-10`).
		'text-card-foreground relative z-0 gap-4 overflow-visible rounded-xl py-4 text-sm has-data-[slot=card-footer]:pb-0 has-data-[slot=listing-pagination]:pb-0 has-[>img:first-child]:pt-0 data-[size=sm]:gap-3 data-[size=sm]:py-3 data-[size=sm]:has-data-[slot=card-footer]:pb-0 data-[size=sm]:has-data-[slot=listing-pagination]:pb-0 *:[img:first-child]:rounded-t-xl *:[img:last-child]:rounded-b-xl group/card flex flex-col focus-within:z-[1] has-[[role=listbox]]:z-[1]',
		glass ? 'glass-panel' : ELEVATION[elevation],
		className
	)}
	{...restProps}
>
	{@render children?.()}
</div>
