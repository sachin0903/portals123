<script lang="ts">
	// An identity check's analysis: its scores and facts as one row of small stats, then its
	// outcomes grouped by analysis, then its specimen links beside the legend. Laid out by its own
	// width (container queries). The AML console and the firm's compliance tab show it alike, each
	// under the check's photos.
	import type { Snippet } from 'svelte';
	import ExternalLinkIcon from '@lucide/svelte/icons/external-link';
	import { humanise } from '../../../sentence-case.js';
	import { isWebUrl } from '../../../urls.js';
	import {
		OUTCOME_STYLES,
		outcomeKind,
		scorePercent,
		type IdentityAnalysis,
		type IdentityOutcome
	} from './identity-analysis.js';

	let {
		analysis,
		breakdown = [],
		empty = 'No breakdown was given.',
		pending,
		compact = false,
		show = 'all'
	}: {
		/** The check's analysis. Null when its result has none to give. */
		analysis: IdentityAnalysis | null;
		/** The result's outcomes as one flat list, shown when the analysis has no groups. */
		breakdown?: IdentityOutcome[];
		/** What shows when there is no outcome to list. */
		empty?: string;
		/** Shown in place of the outcomes while the check waits on its result; the links and the
		 *  legend wait with them. */
		pending?: Snippet;
		/** Smaller type and closer lines, for a page that has to fit one screen. */
		compact?: boolean;
		/** Which part to show: all of it, only the scores and facts, or only the outcomes with their
		 *  links and legend — for a page that lays the two out apart. */
		show?: 'all' | 'scores' | 'outcomes';
	} = $props();

	const links = $derived((analysis?.links ?? []).filter((l) => l.urls.some(isWebUrl)));
</script>

{#snippet outcomeItem(label: string, outcome: string)}
	{@const o = OUTCOME_STYLES[outcomeKind(outcome)]}
	<li class="flex items-center gap-2">
		<o.icon class="{compact ? 'size-3.5' : 'size-4'} shrink-0 {o.class}" aria-hidden="true" />
		<span class="min-w-0">{label}</span>
		<span class="sr-only">: {o.word}</span>
	</li>
{/snippet}

<div class={['@container', compact ? 'space-y-2 text-xs' : 'space-y-3 text-sm']}>
	<!-- The scores and facts, as one row of small stats. -->
	{#if show !== 'outcomes' && analysis && (analysis.scores.length || analysis.facts.length)}
		<ul class="flex flex-wrap gap-1.5">
			{#each analysis.scores as score (score.key)}
				<li class="bg-muted/40 flex items-baseline gap-1.5 rounded-md border px-2 py-1 text-xs">
					<span class="text-sm font-semibold tabular-nums">{scorePercent(score.value)}</span>
					<span class="text-muted-foreground">{score.label}</span>
				</li>
			{/each}
			{#each analysis.facts as fact (fact.key)}
				<li class="bg-muted/40 flex items-baseline gap-1.5 rounded-md border px-2 py-1 text-xs">
					<span class="text-sm font-medium">{humanise(fact.value)}</span>
					<span class="text-muted-foreground">{fact.label}</span>
				</li>
			{/each}
		</ul>
	{/if}

	<!-- The outcomes, grouped as the analysis groups them. -->
	{#if show === 'scores'}
		<!-- Shown apart, by the caller. -->
	{:else if pending}
		{@render pending()}
	{:else if analysis && analysis.groups.length}
		<div class={['grid gap-x-6 @md:grid-cols-2 @3xl:grid-cols-3', compact ? 'gap-y-2' : 'gap-y-3']}>
			{#each analysis.groups as group (group.key)}
				<div class={compact ? 'space-y-1' : 'space-y-2'}>
					<p class="text-muted-foreground text-[0.7rem] font-semibold tracking-wide uppercase">
						{group.label}
					</p>
					<ul class={compact ? 'space-y-1' : 'space-y-1.5'}>
						{#each group.items as item (item.key)}
							{@render outcomeItem(item.label, item.outcome)}
						{/each}
					</ul>
				</div>
			{/each}
		</div>
	{:else if breakdown.length}
		<ul class="grid gap-x-6 gap-y-1.5 @md:grid-cols-2 @3xl:grid-cols-3">
			{#each breakdown as item (item.key)}
				{@render outcomeItem(item.label, item.outcome)}
			{/each}
		</ul>
	{:else}
		<p class="text-muted-foreground text-xs">{empty}</p>
	{/if}

	{#if !pending && show !== 'scores'}
		<!-- The specimen links and the legend share one line, wrapping only when it runs out. -->
		<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
				{#each links as link (link.key)}
					<span class="flex flex-wrap items-center gap-x-2">
						<span class="text-muted-foreground">{link.label}:</span>
						{#each link.urls.filter(isWebUrl) as url, i (url)}
							<a
								href={url}
								target="_blank"
								rel="noopener noreferrer"
								referrerpolicy="no-referrer"
								title={url}
								class="text-primary inline-flex items-center gap-0.5 hover:underline"
							>
								{i + 1}<ExternalLinkIcon class="size-3" />
							</a>
						{/each}
					</span>
				{/each}
			</div>
			<div class="text-muted-foreground ml-auto flex flex-wrap gap-x-2.5 gap-y-1">
				{#each Object.entries(OUTCOME_STYLES) as [kind, o] (kind)}
					<span class="flex items-center gap-1">
						<o.icon class="size-3 {o.class}" aria-hidden="true" />{o.word}
					</span>
				{/each}
			</div>
		</div>
	{/if}
</div>
