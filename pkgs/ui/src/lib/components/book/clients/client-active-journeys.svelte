<script lang="ts">
	// The overview's slim notice that this client is on a journey. Settled journeys stay on the
	// Journeys section; this only names the ones still open, and how far each has got.
	import RouteIcon from '@lucide/svelte/icons/route';
	import { cn } from '../../../utils.js';
	import { alertVariants } from '../../ui/alert/index.js';
	import { activeJourneys, type ClientJourneySummary } from './client-journey.js';

	let {
		journeys = [],
		href
	}: {
		journeys?: readonly ClientJourneySummary[];
		/** This journey, read on this client's record. */
		href?: (uid: string) => string;
	} = $props();

	const open = $derived(activeJourneys(journeys));

	function pct(journey: ClientJourneySummary): number {
		return journey.steps_total ? Math.round((journey.steps_done / journey.steps_total) * 100) : 0;
	}
</script>

{#if open.length}
	<div
		role="status"
		class={cn(alertVariants({ variant: 'info' }), 'flex items-start gap-2.5 py-2')}
	>
		<RouteIcon class="mt-0.5 size-4 shrink-0 translate-y-0" aria-hidden="true" />
		<div class="min-w-0 flex-1 space-y-1.5">
			{#if open.length > 1}
				<p class="text-xs font-medium">On {open.length} active journeys</p>
			{/if}
			{#each open as journey (journey.uid)}
				{@const target = href?.(journey.uid)}
				<svelte:element
					this={target ? 'a' : 'div'}
					href={target}
					class="group flex min-w-0 items-center gap-3 text-sm"
				>
					{#if open.length === 1}
						<span class="shrink-0">On an active journey</span>
						<span aria-hidden="true">·</span>
					{/if}
					<span class="min-w-0 truncate font-medium {target ? 'group-hover:underline' : ''}">
						{journey.flow_name}
					</span>
					{#if journey.steps_total}
						<span class="ml-auto flex shrink-0 items-center gap-2.5">
							<span
								class="bg-current/15 h-1.5 w-24 overflow-hidden rounded-full"
								role="progressbar"
								aria-label="{journey.flow_name} progress"
								aria-valuemin="0"
								aria-valuemax="100"
								aria-valuenow={pct(journey)}
								aria-valuetext="{journey.steps_done} of {journey.steps_total}"
							>
								<span class="block h-full rounded-full bg-current" style:width="{pct(journey)}%"
								></span>
							</span>
							<span class="text-right text-xs whitespace-nowrap tabular-nums">
								{journey.steps_done} of {journey.steps_total}
							</span>
						</span>
					{:else}
						<span class="ml-auto shrink-0 text-xs">Not started</span>
					{/if}
				</svelte:element>
			{/each}
		</div>
	</div>
{/if}
