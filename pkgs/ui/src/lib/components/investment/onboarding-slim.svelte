<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import type { PortalOnboarding } from './types.js';

	// The 'slim' onboarding look — one ~44px line instead of the tinted CTA panel: a
	// small-caps label, a thin brand progress bar, "N of M steps complete" and a chevron.
	// Self-hides on exactly onboarding-progress's rule (null or done ≥ total ⇒ nothing — a
	// finished client never sees it), and respects interactive={false} by rendering an inert
	// row instead of the /onboarding link.
	let {
		onboarding,
		interactive = true
	}: {
		onboarding: PortalOnboarding | null;
		interactive?: boolean;
	} = $props();

	const show = $derived(onboarding !== null && onboarding.done < onboarding.total);
	const pct = $derived(
		onboarding && onboarding.total > 0
			? Math.max(0, Math.min(100, (onboarding.done / onboarding.total) * 100))
			: 0
	);
</script>

{#snippet inner(o: PortalOnboarding)}
	<span class="text-muted-foreground shrink-0 text-xs font-medium tracking-[0.14em] uppercase">
		Onboarding
	</span>
	<span class="bg-brand/15 h-1 min-w-6 flex-1 overflow-hidden rounded-full">
		<!-- Width is inline (not a class) so SSR renders the final state — no fill-in on load. -->
		<span class="bg-brand block h-full rounded-full" style="width:{pct}%"></span>
	</span>
	<span class="text-muted-foreground shrink-0 text-xs">
		{o.done} of {o.total} steps complete
	</span>
	<ChevronRightIcon class="text-muted-foreground size-4 shrink-0" aria-hidden="true" />
{/snippet}

{#if show && onboarding}
	{#if interactive}
		<!-- min-h-11: the whole line is the touch target (≥44px). -->
		<a
			href="/onboarding"
			class="hover:bg-accent/40 flex min-h-11 items-center gap-3 transition-colors"
		>
			{@render inner(onboarding)}
		</a>
	{:else}
		<!-- The studio preview: same look, no navigation. -->
		<div class="flex min-h-11 items-center gap-3">
			{@render inner(onboarding)}
		</div>
	{/if}
{/if}
