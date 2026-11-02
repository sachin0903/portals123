<script lang="ts">
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import { KIND_SINGULAR, kindOf } from './insights.js';
	import type { NewsItem } from './types.js';

	/**
	 * The `journal` look of the library — the chrome-less signature, what `auto` resolves to
	 * under the ledger finish. No pictures and no cards: a private bank's letter rather than a
	 * magazine. The lead is a headline set large in the display face under a brand rule, with
	 * its standfirst; everything else is a dated column — the date in the margin, the topic as
	 * a small-caps kicker in the brand ink, the headline in the display face — divided by
	 * hairlines. Where the magazine look spends the firm's photography, this one spends its
	 * typeface and its colour, and nothing else.
	 *
	 * Takes the ALREADY-FILTERED list, like every library look.
	 */
	let { items }: { items: NewsItem[] } = $props();

	const lead = $derived(items[0] ?? null);
	const rest = $derived(items.slice(1));
	const display = 'font-family: var(--font-display, var(--font-sans))';
</script>

{#if lead}
	<a href={lead.href} class="group border-brand block border-t-2 pt-5" data-news-lead>
		<p class="flex flex-wrap items-center gap-x-2 text-xs font-medium">
			<span class="text-brand-strong tracking-[0.16em] uppercase">{lead.source}</span>
			<span class="text-muted-foreground">
				{[KIND_SINGULAR[kindOf(lead)], lead.time].filter(Boolean).join(' · ')}
			</span>
		</p>
		<h2
			class="@lg:text-4xl mt-3 max-w-3xl text-3xl leading-[1.1] font-medium tracking-tight text-balance"
			style={display}
		>
			{lead.headline}
		</h2>
		{#if lead.summary}
			<p class="text-muted-foreground mt-3 max-w-2xl text-base leading-relaxed">
				{lead.summary}
			</p>
		{/if}
		<span class="text-brand-strong mt-4 inline-flex items-center gap-1 text-sm font-medium">
			Read
			<ArrowRightIcon class="size-4 transition-transform group-hover:translate-x-0.5" />
		</span>
	</a>

	{#if rest.length > 0}
		<ol class="divide-border mt-4 divide-y border-t">
			{#each rest as item (item.id)}
				<li>
					<a
						href={item.href}
						class="group @md:grid-cols-[7rem_1fr_auto] grid grid-cols-[1fr_auto] items-baseline gap-x-4 gap-y-1 py-4"
					>
						<p
							class="text-muted-foreground @md:order-none order-3 col-span-2 text-xs tabular-nums @md:col-span-1"
						>
							{item.time}
						</p>
						<div class="min-w-0">
							<p class="text-brand-strong text-[0.7rem] font-medium tracking-[0.14em] uppercase">
								{item.source}
								<span class="text-muted-foreground font-normal tracking-normal normal-case">
									· {KIND_SINGULAR[kindOf(item)]}</span
								>
							</p>
							<p
								class="mt-1 text-lg leading-snug font-medium tracking-tight group-hover:underline group-hover:underline-offset-4"
								style={display}
							>
								{item.headline}
							</p>
						</div>
						<ArrowRightIcon
							class="text-muted-foreground group-hover:text-foreground size-4 shrink-0 self-center transition-colors"
						/>
					</a>
				</li>
			{/each}
		</ol>
	{/if}
{/if}
