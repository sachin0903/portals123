<script lang="ts">
	import type { HTMLTableAttributes } from 'svelte/elements';
	import { cn, type WithElementRef } from '../../../utils.js';

	let {
		ref = $bindable(null),
		class: className,
		/** Classes for the scroll CONTAINER rather than the table — how a caller bounds the height so
		 *  the rows scroll inside it (see DataTable's `scroll`). Separate from `class` because the
		 *  container is what owns overflow and the `@container` context; putting a height on the
		 *  `<table>` would do nothing. */
		containerClass,
		children,
		...restProps
	}: WithElementRef<HTMLTableAttributes> & { containerClass?: string } = $props();
</script>

<!-- The wrapper is a size CONTAINER (`@container`): column classes like `hidden @md:table-cell`
     key off the table's real width — its grid span — not the viewport, so a table in a narrow
     block collapses its low-priority columns instead of clipping money columns. It also carries
     the scroll-shadow affordance (see <style>) so a table that still overflows visibly says
     "more content this way" instead of silently cropping. -->
<div
	data-slot="table-container"
	class={cn('table-scroll-shadows @container relative w-full overflow-x-auto', containerClass)}
>
	<!-- `min-w-full` without a default `w-full`: width 100% stops the table growing past the
	     pane, which is what made nowrap cells overlap on a phone. From `md` it fills the pane
	     again so `table-fixed` percentage columns have a definite width to share. -->
	<table
		bind:this={ref}
		data-slot="table"
		class={cn('min-w-full caption-bottom text-xs md:w-full', className)}
		{...restProps}
	>
		{@render children?.()}
	</table>
</div>

<style>
	/* Pure-CSS scroll shadows (the background-attachment local/scroll technique): the two radial
	   "shadow" layers are pinned to the visible edges (attachment: scroll) while the two "cover"
	   layers scroll with the content (attachment: local) — so a cover slides exactly over its
	   shadow whenever the content edge is in view, and the shadow only shows where clipped
	   columns actually exist. Dark-scheme aware for free: the shadow is mixed from
	   --foreground, which flips with the theme. The covers are TWO stacked local layers per
	   edge — the glass-panel tint over a solid --background underlay: most of these tables sit
	   on translucent glass cards, where a lone solid cover would print as opaque strips (the
	   panel tint alone masks the shadow there), while the ground underlay keeps the cover
	   working when the panel tint is fully transparent — the LEDGER finish's open sections,
	   where a tint-only cover would vanish and the edge shadow would show always-on at rest.
	   Rows must stay transparent (they are) or they'd paint over the shadows. */
	.table-scroll-shadows {
		background:
			linear-gradient(to right, var(--glass-panel-bg, var(--card)) 30%, transparent) left,
			linear-gradient(to left, var(--glass-panel-bg, var(--card)) 30%, transparent) right,
			linear-gradient(to right, var(--background) 30%, transparent) left,
			linear-gradient(to left, var(--background) 30%, transparent) right,
			radial-gradient(
					farthest-side at 0 50%,
					color-mix(in oklab, var(--foreground) 18%, transparent),
					transparent
				)
				left,
			radial-gradient(
					farthest-side at 100% 50%,
					color-mix(in oklab, var(--foreground) 18%, transparent),
					transparent
				)
				right;
		background-repeat: no-repeat;
		background-size:
			40px 100%,
			40px 100%,
			40px 100%,
			40px 100%,
			14px 100%,
			14px 100%;
		background-attachment: local, local, local, local, scroll, scroll;
	}
</style>
