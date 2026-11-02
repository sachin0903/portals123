<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import type { PortalOnboarding } from './types.js';

	// The "Continue your onboarding" CTA, ported from apps/client's home page into a portal
	// block. Self-hides when progress is unknown or complete — a finished client never sees it
	// (portal-page.svelte mirrors this so a hidden block frees its grid cell too).
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
	<div
		class="bg-brand/15 text-brand flex size-10 shrink-0 items-center justify-center rounded-full"
	>
		<ClipboardListIcon class="size-5" />
	</div>
	<div class="min-w-0 flex-1">
		<p class="font-semibold">Continue your onboarding</p>
		<p class="text-muted-foreground text-sm">{o.done} of {o.total} steps complete</p>
		<div class="bg-brand/15 mt-2 h-1.5 overflow-hidden rounded-full">
			<div class="bg-brand h-full rounded-full" style="width:{pct}%"></div>
		</div>
	</div>
	<ChevronRightIcon class="text-muted-foreground size-5 shrink-0" />
{/snippet}

{#if show && onboarding}
	{#if interactive}
		<!-- rounded-xl (not the app's original 2xl): fixed 2xl bypasses --radius, so a firm's radius
		     token could not reach this card (token-hygiene.test.ts). -->
		<a
			href="/onboarding"
			class="border-brand/30 bg-brand/5 hover:bg-brand/10 flex items-center gap-3 rounded-xl border p-4 transition-colors"
		>
			{@render inner(onboarding)}
		</a>
	{:else}
		<!-- The studio preview: same look, no navigation. -->
		<div class="border-brand/30 bg-brand/5 flex items-center gap-3 rounded-xl border p-4">
			{@render inner(onboarding)}
		</div>
	{/if}
{/if}
