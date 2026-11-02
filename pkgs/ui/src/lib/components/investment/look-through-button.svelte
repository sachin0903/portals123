<script lang="ts">
	// The way into a fund's look-through. It used to be a twelve-pixel "Look through" text link
	// (or, on the client's grouped rows, nothing but a chevron), and clients did not find it. It is
	// now a pill in the firm's own colours with a soft brand glow and a sheen that sweeps on hover —
	// the one deliberately lit control on a holdings row, because it is the one that opens a
	// different kind of page.
	//
	// Colour is all tokens: the fill runs --primary → --brand and the glow is --brand, so a firm's
	// theme is what lights it. The glow is the one exception to "raised sheet, never glow" in
	// docs/design-language.md, and it is an exception for a CTA, not an elevation.
	//
	// With `href` it is an anchor. Without, a `<span>` that only LOOKS like the button — for a row
	// whose whole surface is already the link (holdings-grouped.svelte), where an anchor inside an
	// anchor is invalid HTML.
	import ScanSearchIcon from '@lucide/svelte/icons/scan-search';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import { cn } from '../../utils.js';

	let {
		href,
		size = 'sm',
		label = 'Look through',
		class: className
	}: {
		href?: string;
		/** `sm` sits inside a table cell or list row; `md` is the standalone size. */
		size?: 'sm' | 'md';
		label?: string;
		class?: string;
	} = $props();

	const classes = $derived(
		cn(
			'look-through group/lt',
			size === 'sm' ? 'h-7 gap-1.5 px-2.5 text-xs' : 'h-9 gap-2 px-3.5 text-sm',
			className
		)
	);
</script>

{#snippet inner()}
	<span class="look-through-sheen" aria-hidden="true"></span>
	<ScanSearchIcon class={cn('relative shrink-0', size === 'sm' ? 'size-3.5' : 'size-4')} />
	<span class="relative">{label}</span>
	<ArrowRightIcon
		class={cn(
			'relative shrink-0 transition-transform duration-200 group-hover/lt:translate-x-0.5 motion-reduce:transition-none',
			size === 'sm' ? 'size-3' : 'size-3.5'
		)}
	/>
{/snippet}

{#if href}
	<a {href} class={classes} data-slot="look-through">{@render inner()}</a>
{:else}
	<span class={classes} data-slot="look-through" aria-hidden="true">{@render inner()}</span>
{/if}

<style>
	.look-through {
		position: relative;
		isolation: isolate;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		overflow: hidden;
		border-radius: 9999px;
		font-weight: 600;
		letter-spacing: 0.01em;
		white-space: nowrap;
		color: var(--primary-foreground);
		background-image: linear-gradient(
			135deg,
			var(--primary) 0%,
			color-mix(in oklch, var(--primary) 55%, var(--brand)) 100%
		);
		box-shadow:
			inset 0 0 0 1px color-mix(in oklch, var(--brand) 45%, transparent),
			inset 0 1px 0 color-mix(in oklch, white 22%, transparent),
			0 6px 16px -8px color-mix(in oklch, var(--primary) 70%, transparent),
			0 0 20px -1px color-mix(in oklch, var(--brand) 45%, transparent);
		transition:
			transform 180ms ease,
			box-shadow 220ms ease;
		animation: look-through-breathe 3.6s ease-in-out infinite;
	}
	.look-through:hover,
	.look-through:focus-visible {
		transform: translateY(-1px);
		animation-play-state: paused;
		box-shadow:
			inset 0 0 0 1px color-mix(in oklch, var(--brand) 70%, transparent),
			inset 0 1px 0 color-mix(in oklch, white 28%, transparent),
			0 10px 22px -8px color-mix(in oklch, var(--primary) 75%, transparent),
			0 0 28px 0 color-mix(in oklch, var(--brand) 55%, transparent);
	}
	.look-through:focus-visible {
		outline: 2px solid color-mix(in oklch, var(--brand) 80%, white);
		outline-offset: 2px;
	}
	.look-through:active {
		transform: translateY(0);
	}

	/* The sheen: a diagonal highlight parked off the left edge, swept across on hover. */
	.look-through-sheen {
		position: absolute;
		inset: 0;
		z-index: 0;
		background-image: linear-gradient(
			105deg,
			transparent 35%,
			color-mix(in oklch, white 38%, transparent) 50%,
			transparent 65%
		);
		transform: translateX(-120%);
		transition: transform 0s;
	}
	.look-through:hover .look-through-sheen,
	.look-through:focus-visible .look-through-sheen {
		transform: translateX(120%);
		transition: transform 650ms cubic-bezier(0.2, 0.7, 0.2, 1);
	}

	/* The idle glow breathes, slowly, so the control reads as lit rather than flat. */
	@keyframes look-through-breathe {
		0%,
		100% {
			box-shadow:
				inset 0 0 0 1px color-mix(in oklch, var(--brand) 45%, transparent),
				inset 0 1px 0 color-mix(in oklch, white 22%, transparent),
				0 6px 16px -8px color-mix(in oklch, var(--primary) 70%, transparent),
				0 0 20px -1px color-mix(in oklch, var(--brand) 45%, transparent);
		}
		50% {
			box-shadow:
				inset 0 0 0 1px color-mix(in oklch, var(--brand) 55%, transparent),
				inset 0 1px 0 color-mix(in oklch, white 22%, transparent),
				0 6px 16px -8px color-mix(in oklch, var(--primary) 70%, transparent),
				0 0 30px 1px color-mix(in oklch, var(--brand) 58%, transparent);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.look-through {
			animation: none;
			transition: none;
		}
		.look-through:hover,
		.look-through:focus-visible {
			transform: none;
		}
		.look-through-sheen {
			display: none;
		}
	}
</style>
