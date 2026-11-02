<script lang="ts" module>
	// Unique gradient id per instance — several logos can share a page (header + footer).
	let uid = 0;
</script>

<script lang="ts">
	import { cn } from '../../utils.js';
	import { logoSources } from './logo-sources.js';

	let {
		/** `full` = mark + wordmark; `mark` = the mark on its own. */
		variant = 'full',
		/** Sizing/colour for the wrapper (wordmark inherits `currentColor`). */
		class: className,
		/** Sizing for the mark SVG; defaults scale with the wordmark. */
		markClass,
		/** Sizing for the built-in wordmark TEXT. Given alongside a taller `markClass` so the two
		 *  halves of the lockup grow together — a bigger mark beside a fixed 1.125rem name reads as
		 *  a misdrawn logo rather than a larger one. */
		nameClass,
		/** White-label: a tenant logo image URL. When set, replaces the built-in Graphene mark. */
		src,
		/** Optional dark-mode logo variant (falls back to `src`). */
		darkSrc,
		/** White-label icon/symbol image — shown instead of `src` in the `mark` (icon) variant, so a
		 * firm can have a full wordmark AND a compact symbol. Falls back to `src`, then the built-in mark. */
		iconSrc,
		/** White-label wordmark text; defaults to "Graphene". */
		name = 'Graphene'
	}: {
		variant?: 'full' | 'mark';
		class?: string;
		markClass?: string;
		nameClass?: string;
		src?: string;
		darkSrc?: string;
		iconSrc?: string;
		name?: string;
	} = $props();

	const gradId = `graphene-mark-${uid++}`;
	// Which image(s) to render, per the fallback rules in ./logo-sources.ts (pure + unit-tested: the
	// dark variant used to be dropped whenever `variant === 'mark'`, even when the light source had
	// fallen back to the full logo, so a firm's dark logo never appeared).
	const sources = $derived(logoSources({ variant, src, darkSrc, iconSrc }));
	const imgSrc = $derived(sources.img);
	const imgDarkSrc = $derived(sources.dark);
</script>

{#snippet mark(extra: string | undefined)}
	<svg
		viewBox="0 0 426 372"
		class={cn('w-auto', extra)}
		role="img"
		aria-label="Graphene"
		xmlns="http://www.w3.org/2000/svg"
	>
		<defs>
			<!-- The built-in mark's gradient runs --primary → --brand rather than three fixed navies.
			     This matters: a firm that hasn't uploaded a logo yet still gets the mark in ITS colours
			     instead of Graphene's navy, and in dark mode the mark lifts with the tokens instead of
			     disappearing into the sidebar. Under the stock theme it reproduces the original navy
			     ramp almost exactly. -->
			<linearGradient id={gradId} x1="0" y1="0" x2="1" y2="1">
				<stop offset="0%" stop-color="var(--primary)" />
				<stop offset="55%" stop-color="color-mix(in oklch, var(--primary) 70%, var(--brand))" />
				<stop offset="100%" stop-color="color-mix(in oklch, var(--primary) 45%, var(--brand))" />
			</linearGradient>
		</defs>
		<path
			fill="url(#{gradId})"
			d="M110 0 318 0 338 33 350 55 141 55 64 188 141 319 289 318 333 232 365 289 323 372 107 372 0 189Z"
		/>
		<path fill="url(#{gradId})" d="M209 129 393 129 426 186 397 244 360 183 240 183 210 130Z" />
	</svg>
{/snippet}

{#if imgSrc}
	<!-- White-label: tenant logo/icon image. Dark variant shown via the `dark:` swap when provided.
	     `width`/`height` give the browser an aspect ratio (4:1, a typical wordmark) to size the box
	     from BEFORE the bytes decode — CSS keeps the rendered height, the attributes stop the slot
	     collapsing to zero width and shifting the header when the image lands. The API and
	     `logoSources` already refuse the one shape that made this img the LCP: a multi-megapixel
	     data URI decoded on the main thread. -->
	<span class={cn('flex items-center', className)}>
		<img
			src={imgSrc}
			alt={name}
			width="112"
			height="28"
			class={cn('h-7 w-auto object-contain', imgDarkSrc && 'dark:hidden', markClass)}
		/>
		{#if imgDarkSrc}
			<img
				src={imgDarkSrc}
				alt={name}
				width="112"
				height="28"
				class={cn('hidden h-7 w-auto object-contain dark:block', markClass)}
			/>
		{/if}
	</span>
{:else if variant === 'mark'}
	{@render mark(cn('h-7', markClass ?? className))}
{:else}
	<span class={cn('flex items-center gap-2.5', className)}>
		{@render mark(markClass ?? 'h-7')}
		<span class={cn('text-lg font-semibold tracking-[0.04em] uppercase', nameClass)}>{name}</span>
	</span>
{/if}
