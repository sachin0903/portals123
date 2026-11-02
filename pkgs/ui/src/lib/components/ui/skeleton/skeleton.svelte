<script lang="ts">
	import { cn, type WithElementRef, type WithoutChildren } from '../../../utils.js';
	import type { HTMLAttributes } from 'svelte/elements';

	/**
	 * The placeholder block every skeleton is built from — one definition, so the loading look is
	 * the same in all fourteen places that draw one (`ListingSkeleton`, the sidebar menu, and the
	 * streamed listings).
	 *
	 * A LEFT-TO-RIGHT SWEEP, not `animate-pulse`. A pulse fades the whole block in and out, which
	 * reads as something blinking at you; a sweep travels, and travel is what says "this is filling
	 * in" rather than "this is broken". It is also directional in the same sense as reading, so a
	 * column of them scans as one surface rather than fourteen independent flashes.
	 *
	 * The fill is `--skeleton`, not `--muted`, because a skeleton's surface is `--card` — white in
	 * the light themes — and muted is a 0.035 lightness step off it. At that contrast a loading
	 * page reads as an EMPTY one, which is the bug this token was added for; the sweep made it
	 * worse, lightening an already near-white block towards the surface it sits on.
	 */
	let {
		ref = $bindable(null),
		class: className,
		...restProps
	}: WithoutChildren<WithElementRef<HTMLAttributes<HTMLDivElement>>> = $props();
</script>

<div
	bind:this={ref}
	data-slot="skeleton"
	class={cn('bg-skeleton relative overflow-hidden rounded-md', className)}
	{...restProps}
></div>

<style>
	/* Selected by the data-slot ATTRIBUTE, which is a literal in the markup above. The class list is
	   composed through `cn()`, and Svelte prunes a scoped rule whose selector it cannot see there —
	   a pruned animation leaves a static grey block that every other test still passes, which is the
	   lesson `risk-badge.ssr.test.ts` was written for. `skeleton.ssr.test.ts` asserts on the
	   compiler's own output for the same reason. */
	[data-slot='skeleton']::after {
		content: '';
		position: absolute;
		inset: 0;
		/* Travels a full width either side, so the band is off-screen at both ends and the rest is
		   clean muted rather than a highlight parked at an edge. */
		transform: translateX(-100%);
		animation: skeleton-sweep 1.6s ease-in-out infinite;
		background: linear-gradient(
			90deg,
			transparent 0%,
			color-mix(in oklch, var(--card) 45%, transparent) 50%,
			transparent 100%
		);
	}

	/* `--card` rather than a literal white: the sweep is the SURFACE passing over the placeholder,
	   so it lightens in light mode and darkens in dark without a branch — and a firm re-branding its
	   surface re-tints this with everything else. No palette colour appears here, the rule
	   `tones.ts` sets. Held at 45%: the band has to stay clearly inside the block, because at the
	   old 60% over a `--muted` fill the brightest point of the sweep was indistinguishable from the
	   card and the bar appeared to break in half as it passed. */
	@keyframes skeleton-sweep {
		to {
			transform: translateX(100%);
		}
	}

	/* The skeleton's JOB — showing the shape that is arriving — is done by the block itself, so
	   reduced motion simply loses the sweep. Nothing is lost but the movement, unlike the nav
	   progress bar, where the motion IS the signal and a static bar is substituted. */
	@media (prefers-reduced-motion: reduce) {
		[data-slot='skeleton']::after {
			animation: none;
			background: none;
		}
	}
</style>
