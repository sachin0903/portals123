<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import OnboardingStages from './onboarding-stages.svelte';
	import type { PortalOnboarding } from './types.js';

	// The 'milestones' onboardingProgress look — the numbered-circle stepper (onboarding-stages.svelte)
	// across the WHOLE journey (details -> verification -> sign -> setup -> complete), not just a
	// percentage, wrapped in the same clickable-card chrome the other two looks use. `stages` is
	// additive on the API response (added alongside the pre-existing done/total): a stale cached
	// response or a very old API build carries neither `stages` nor a populated array, so this
	// falls back to the plain progress bar rather than rendering a brand-new empty stepper.
	let {
		onboarding,
		interactive = true
	}: {
		onboarding: PortalOnboarding | null;
		interactive?: boolean;
	} = $props();

	const show = $derived(onboarding !== null && onboarding.done < onboarding.total);
	const stages = $derived(onboarding?.stages ?? []);
	/** Steps the client can act on now. Absent on an older response ⇒ 0, i.e. say nothing. */
	const waiting = $derived(Math.max(0, onboarding?.available ?? 0));
	const pct = $derived(
		onboarding && onboarding.total > 0
			? Math.max(0, Math.min(100, (onboarding.done / onboarding.total) * 100))
			: 0
	);
</script>

{#snippet stepper(o: PortalOnboarding)}
	<div class="mb-2 flex items-center justify-between gap-2">
		<div class="min-w-0">
			<p class="font-semibold">Your onboarding journey</p>
			{#if waiting > 0}
				<!-- The card is the first thing on the client's home page, and a stepper alone states a
				     fact without asking for anything. `available` is the only count that can be phrased
				     as a call to action: most of `total - done` is held behind the adviser's own work,
				     so counting that would summon a client to work they cannot yet do. -->
				<p class="text-brand text-sm font-medium">
					{waiting} step{waiting === 1 ? '' : 's'} need{waiting === 1 ? 's' : ''} you
				</p>
			{/if}
		</div>
		<ChevronRightIcon class="text-muted-foreground size-5 shrink-0" aria-hidden="true" />
	</div>
	{#if stages.length}
		<OnboardingStages {stages} />
	{:else}
		<!-- No stage data (older cached response) -- the same flat bar the other looks show. -->
		<p class="text-muted-foreground mb-2 text-sm">{o.done} of {o.total} steps complete</p>
		<div class="bg-brand/15 h-1.5 overflow-hidden rounded-full">
			<div class="bg-brand h-full rounded-full" style="width:{pct}%"></div>
		</div>
	{/if}
{/snippet}

{#if show && onboarding}
	{#if interactive}
		<a
			href="/onboarding"
			class="border-border bg-card hover:bg-accent/40 block rounded-xl border p-4 transition-colors"
		>
			{@render stepper(onboarding)}
		</a>
	{:else}
		<!-- The studio preview: same look, no navigation. -->
		<div class="border-border bg-card block rounded-xl border p-4">
			{@render stepper(onboarding)}
		</div>
	{/if}
{/if}
