<script lang="ts">
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import { Badge } from '../ui/badge/index.js';
	import * as Card from '../ui/card/index.js';
	import InsightCover from './insight-cover.svelte';
	import { stampOf, topicSlots } from './insights.js';
	import WidgetHeader from './widget-header.svelte';
	import type { NewsItem } from './types.js';

	/**
	 * The home-page news card (the `feed` variant). Three looks, the same axis as the library's:
	 * `card` is the headline list; `magazine` puts the newest piece on a cover — its hero, or
	 * the firm's palette through `InsightCover` — above the next few headlines; `journal` is the
	 * chrome-less dated column in the display face, for the ledger finish.
	 */
	let {
		items,
		look = 'card',
		viewAllHref = '/insights',
		interactive = true
	}: {
		items: NewsItem[];
		/** `card` | `magazine` | `journal` — resolved by `looks.ts`; anything else renders `card`. */
		look?: string;
		/** The firm's content library — the page hosting a `library` news block, decided from the
		 *  firm's own page set (listingLinks), so a firm that dropped its Insights page gets no
		 *  link to a 404. Null for no link. */
		viewAllHref?: string | null;
		interactive?: boolean;
	} = $props();

	const lead = $derived(items[0] ?? null);
	const rest = $derived(items.slice(1));
	const slots = $derived(topicSlots(items));
	const display = 'font-family: var(--font-display, var(--font-sans))';
</script>

{#if look === 'journal'}
	<!-- No card: a hairline column on the page ground, like the ledger's other signatures. -->
	<div class="@container flex h-full flex-col" data-news-feed data-look="journal">
		<WidgetHeader bare title="Market news" href={viewAllHref} {interactive} class="px-0" />
		<ol class="border-brand mt-2 divide-y border-t-2">
			{#each items as item (item.id)}
				<li>
					<a href={item.href} class="group block py-3">
						<p class="flex flex-wrap items-center gap-x-2 text-[0.7rem] font-medium">
							<span class="text-brand-strong tracking-[0.14em] uppercase">{item.source}</span>
							<span class="text-muted-foreground tabular-nums">{item.time}</span>
						</p>
						<p
							class="mt-1 line-clamp-2 leading-snug font-medium tracking-tight group-hover:underline group-hover:underline-offset-4"
							style={display}
						>
							{item.headline}
						</p>
					</a>
				</li>
			{/each}
		</ol>
	</div>
{:else if look === 'magazine' && lead}
	<Card.Root glass class="h-full overflow-hidden" data-news-feed data-look="magazine">
		<WidgetHeader title="Market news" href={viewAllHref} {interactive} />
		<Card.Content class="@container space-y-1">
			<a
				href={lead.href}
				class="group relative block aspect-[16/9] overflow-hidden rounded-lg"
				data-news-lead
			>
				<InsightCover
					item={lead}
					slot={slots.get(lead.source) ?? 0}
					eager
					class="absolute inset-0"
				/>
				<div
					class="absolute inset-0 bg-gradient-to-t from-[color-mix(in_oklch,var(--hero-base)_88%,transparent)] via-[color-mix(in_oklch,var(--hero-base)_30%,transparent)] to-transparent"
					aria-hidden="true"
				></div>
				<div class="absolute inset-x-0 bottom-0 p-4 text-[var(--hero-foreground)]">
					<p class="text-[0.7rem] font-medium tracking-[0.14em] uppercase opacity-85">
						{lead.source}
					</p>
					<p
						class="mt-1 line-clamp-2 text-lg leading-snug font-medium tracking-tight"
						style={display}
					>
						{lead.headline}
					</p>
				</div>
			</a>
			{#if rest.length > 0}
				<div class="divide-y">
					{#each rest as item (item.id)}
						<a
							href={item.href}
							class="hover:bg-accent/35 group -mx-3 flex items-start gap-3 rounded-lg px-3 py-2.5 transition-colors"
						>
							<div class="min-w-0 flex-1">
								<p class="text-brand-strong text-[0.65rem] font-medium tracking-[0.14em] uppercase">
									{item.source}
								</p>
								<p class="mt-0.5 line-clamp-2 text-sm leading-snug font-medium">{item.headline}</p>
								<p class="text-muted-foreground mt-1 text-xs">{stampOf(item)}</p>
							</div>
							<ArrowUpRightIcon
								class="text-muted-foreground mt-0.5 size-4 shrink-0 transition-colors group-hover:text-foreground"
							/>
						</a>
					{/each}
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
{:else}
	<!-- The same card chrome and the same one-row heading as every sibling widget: the label-over-
	     title pair ("Market news / Latest updates") and the decorative icon said nothing the
	     headlines below did not. -->
	<Card.Root glass class="h-full" data-news-feed data-look="card">
		<WidgetHeader title="Market news" href={viewAllHref} {interactive} />
		<Card.Content class="divide-y">
			{#each items as item (item.id)}
				<a
					href={item.href}
					class="hover:bg-accent/35 group -mx-3 flex gap-3 rounded-lg px-3 py-3 transition-colors first:pt-2 last:pb-2"
				>
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-2">
							<p class="text-muted-foreground text-xs font-medium">{item.source}</p>
							{#if item.tag}
								<Badge variant="outline" class="h-5 px-1.5 text-[0.65rem]">{item.tag}</Badge>
							{/if}
						</div>
						<p class="mt-1 line-clamp-2 font-medium leading-snug">{item.headline}</p>
						<p class="text-muted-foreground mt-1 line-clamp-2 text-xs leading-relaxed">
							{item.summary}
						</p>
						<p class="text-muted-foreground mt-2 text-xs">{item.time}</p>
					</div>
					<ArrowUpRightIcon
						class="text-muted-foreground mt-0.5 size-4 shrink-0 transition-colors group-hover:text-foreground"
					/>
				</a>
			{/each}
		</Card.Content>
	</Card.Root>
{/if}
