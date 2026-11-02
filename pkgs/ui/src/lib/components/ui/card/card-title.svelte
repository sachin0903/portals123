<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn, type WithElementRef } from '../../../utils.js';

	let {
		ref = $bindable(null),
		class: className,
		children,
		...restProps
	}: WithElementRef<HTMLAttributes<HTMLDivElement>> = $props();
</script>

<!-- role/aria-level: every card announces its title as a heading, so screen-reader heading
     navigation stops skipping the entire card grid. A div with role="heading" (not an h2) keeps
     this token-neutral — no user-agent heading styles appear, nothing visual changes — and both
     attributes sit BEFORE the spread so a caller embedding a card under a deeper section can
     override the level via restProps.
     Size/tracking/case moved off the class list into theme.css's `[data-slot='card-title']`
     components-layer rule (the finish-axis --card-title-* tokens; defaults equal the old
     `text-lg tracking-tight`). Caller classes still win — utilities beat the components layer. -->
<div
	bind:this={ref}
	role="heading"
	aria-level={2}
	data-slot="card-title"
	class={cn('leading-snug font-semibold', className)}
	{...restProps}
>
	{@render children?.()}
</div>
