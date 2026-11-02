<script lang="ts">
	import { finish, topicFinish } from '../../categorical.js';
	import { cn } from '../../utils.js';
	import type { NewsItem } from './types.js';

	/**
	 * The picture on a piece of the firm's content — its hero when it has one, and when it does
	 * not, a COVER in the firm's own palette rather than a grey box or a stock gradient: the
	 * topic's card finish (`categorical.ts`, built off `--brand`, so every firm's covers are in
	 * its colours), and the topic's initial
	 * set large in the display face. A firm with no photography still gets an Insights page that
	 * looks like its own.
	 *
	 * A hero that fails to load (an asset purged from the store, a dead CDN) falls back to the
	 * same cover rather than a broken-image glyph.
	 */
	let {
		item,
		slot = 0,
		eager = false,
		class: className
	}: {
		item: NewsItem;
		/** The topic's slot — `topicSlots()` across the library; picks its finish. */
		slot?: number;
		/** The lead's picture is above the fold; everything else may lazy-load. */
		eager?: boolean;
		class?: string;
	} = $props();

	let broken = $state<string | null>(null);
	const showImage = $derived(!!item.heroUrl && broken !== item.id);
	const initial = $derived((item.source.trim()[0] ?? item.headline[0] ?? '').toUpperCase());
</script>

{#if showImage}
	<img
		src={item.heroUrl}
		alt=""
		class={cn('block h-full w-full object-cover', className)}
		loading={eager ? 'eager' : 'lazy'}
		onerror={() => (broken = item.id)}
	/>
{:else}
	<div
		class={cn(
			'@container relative block h-full w-full overflow-hidden text-[var(--hero-foreground)]',
			className
		)}
		style={finish(topicFinish(slot))}
		aria-hidden="true"
		data-insight-cover
	>
		<!-- The specular hairline every ink surface wears (the same utility the portfolio header
		     draws), so a cover and the header read as one family — and it follows the accent token
		     rather than a fixed white. -->
		<span class="hero-hairline pointer-events-none"></span>
		<!-- Top-right, not bottom: the magazine lead lays an ink wash over the foot of the cover for
		     its headline, and a glyph under that wash is a glyph nobody sees. No kind label on the
		     cover itself — every caller prints the kind and topic beside it, and twice is noise. -->
		<p
			class="absolute top-1 right-4 text-[clamp(3rem,30cqw,7rem)] leading-none font-medium tracking-tight opacity-25 select-none"
			style="font-family: var(--font-display, var(--font-sans))"
		>
			{initial}
		</p>
	</div>
{/if}
