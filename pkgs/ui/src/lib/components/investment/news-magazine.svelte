<script lang="ts">
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import InsightCover from './insight-cover.svelte';
	import { stampOf, topicSlots } from './insights.js';
	import type { NewsItem } from './types.js';

	/**
	 * The `magazine` look of the library: a COVER STORY, then the rest as picture cards in a
	 * grid. The one Insights design that is led by the firm's own pictures — and, through
	 * `InsightCover`, by its own palette when it has none — because this page is where a firm's
	 * identity has the room the account pages do not give it. Headlines are set in the display
	 * face so a firm's typographic choice shows here first.
	 *
	 * Takes the ALREADY-FILTERED list: the library owns the kind switch, topic pills and search,
	 * and every look renders whatever survives them.
	 */
	let { items }: { items: NewsItem[] } = $props();

	const lead = $derived(items[0] ?? null);
	const rest = $derived(items.slice(1));
	const slots = $derived(topicSlots(items));
	const slotOf = (item: NewsItem) => slots.get(item.source) ?? 0;
</script>

{#if lead}
	<a
		href={lead.href}
		class="group @lg:block @lg:aspect-[16/7] relative flex flex-col overflow-hidden rounded-xl bg-[var(--hero-base)] shadow-[var(--hero-shadow)] transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5"
		data-news-lead
	>
		<!-- Narrow: the picture, then the words on the ink below it — overlaid on a phone, a real
		     headline plus summary covered the whole photograph and a long one ran off its top.
		     Wide: the words sit on an ink wash that rises from the foot, so a light photograph and
		     a face in the brand's palette both carry white type. -->
		<div class="@lg:absolute @lg:inset-0 @lg:aspect-auto relative aspect-[16/10]">
			<InsightCover item={lead} slot={slotOf(lead)} eager class="absolute inset-0" />
			<div
				class="@lg:inset-0 @lg:h-auto @lg:from-[color-mix(in_oklch,var(--hero-base)_88%,transparent)] @lg:via-[color-mix(in_oklch,var(--hero-base)_40%,transparent)] absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-[var(--hero-base)] to-transparent"
				aria-hidden="true"
			></div>
		</div>
		<div
			class="@lg:absolute @lg:inset-x-0 @lg:bottom-0 @lg:p-7 relative p-5 pt-1 text-[var(--hero-foreground)]"
		>
			<p class="flex flex-wrap items-center gap-x-2 text-xs font-medium">
				<span
					class="rounded-full bg-[var(--hero-foreground)]/15 px-2 py-0.5 tracking-wide uppercase backdrop-blur-sm"
					>{lead.source}</span
				>
				<span class="text-[var(--hero-muted)]">{stampOf(lead)}</span>
			</p>
			<h2
				class="@lg:text-3xl mt-2 max-w-3xl text-xl leading-tight font-medium tracking-tight text-balance"
				style="font-family: var(--font-display, var(--font-sans))"
			>
				{lead.headline}
			</h2>
			{#if lead.summary}
				<p class="mt-2 line-clamp-2 max-w-2xl text-sm leading-relaxed text-[var(--hero-muted)]">
					{lead.summary}
				</p>
			{/if}
			<span class="mt-3 inline-flex items-center gap-1 text-sm font-medium">
				Read
				<ArrowRightIcon class="size-4 transition-transform group-hover:translate-x-0.5" />
			</span>
		</div>
	</a>

	{#if rest.length > 0}
		<div class="@md:grid-cols-2 @3xl:grid-cols-3 grid gap-4">
			{#each rest as item (item.id)}
				<a
					href={item.href}
					class="glass-panel group flex flex-col overflow-hidden rounded-xl transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-md"
					data-news-card
				>
					<div class="aspect-[16/10] w-full overflow-hidden">
						<InsightCover {item} slot={slotOf(item)} />
					</div>
					<div class="flex flex-1 flex-col gap-1.5 p-4">
						<p class="text-brand-strong text-[0.7rem] font-medium tracking-[0.14em] uppercase">
							{item.source}
						</p>
						<h3
							class="line-clamp-3 text-lg leading-snug font-medium tracking-tight"
							style="font-family: var(--font-display, var(--font-sans))"
						>
							{item.headline}
						</h3>
						<p class="text-muted-foreground mt-auto pt-1 text-xs">{stampOf(item)}</p>
					</div>
				</a>
			{/each}
		</div>
	{/if}
{/if}
