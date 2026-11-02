<script lang="ts">
	import { accountFace } from '../../categorical.js';
	import { useCardDesigns } from '../../card-designs.js';
	import { Money } from '../ui/money/index.js';
	import { groupWrappers, type GroupAxis } from './grouping.js';
	import { fallbackHref } from './account-stack.svelte';
	import { cn } from '../../utils.js';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import type { Wrapper } from './types.js';

	// The accounts 'float' look: wrapper-face CHIPS laid loose on the page ground — physical cards
	// spread on a desk, not a widget in a white box. Each chip reuses the stack's card finishes
	// (categorical.ts: one finish per product type, built off the firm's own brand, so ISA/SIPP/GIA
	// stay distinguishable without leaving the palette) but drops the deck mechanics entirely: no
	// overlap, no fan-open, just a responsive cluster with air between the chips.
	//
	// Deliberately NO heading: the chips are self-describing (every one wears its product type),
	// and an eyebrow above a loose cluster would re-frame it as a section — the whole point of this
	// look is that the accounts sit directly on the ground.
	let {
		wrappers,
		groupBy = 'none',
		interactive = true
	}: {
		wrappers: Wrapper[];
		/** 'wrapper'/'owner' clusters the chips into groups with a small-caps eyebrow + subtotal
		 * per group (grouping.ts — the same buckets every accounts variant files into). */
		groupBy?: GroupAxis;
		/** False stills the hover lift (studio preview). */
		interactive?: boolean;
	} = $props();

	const sections = $derived(groupBy === 'none' ? null : groupWrappers(wrappers, groupBy));

	// The stack's card finishes (categorical.ts `accountFace`): one map, so an account wears the
	// same face here as on the deck and the cards.
	const cardDesigns = useCardDesigns();
	const faceFor = (type: string) => accountFace(type, cardDesigns());
</script>

{#snippet chip(w: Wrapper)}
	<a
		href={w.href ?? fallbackHref(w.type)}
		aria-label="{w.name} — {w.owner}"
		class={cn(
			// hero-surface carries the ink foreground, --hero-shadow and the overflow clip as
			// tokens; the per-type face overrides its background (and, when pale, its ink). rounded-xl is
			// deliberate even under the ledger finish (--radius:0): a physical chip keeps its
			// corner — it is an OBJECT on the page, not a surface of it.
			'hero-surface block rounded-xl p-4',
			interactive &&
				'transition-transform duration-200 hover:-translate-y-1 focus-visible:-translate-y-1 motion-reduce:transition-none motion-reduce:hover:translate-y-0 motion-reduce:focus-visible:translate-y-0'
		)}
		style={faceFor(w.type)}
	>
		<!-- Specular sheen, matching the stack: keyed off --glass-highlight so it dims with the
		     sheen strength a tenant sets rather than always painting a fixed white glint. -->
		<div
			class="pointer-events-none absolute inset-0"
			style="background-image: radial-gradient(120% 80% at 0% 0%, color-mix(in oklch, var(--glass-highlight) 28%, transparent), transparent 58%);"
		></div>
		<div class="relative">
			<div class="flex items-center gap-2">
				<span
					class="bg-[var(--hero-foreground)]/15 rounded-full px-2.5 py-0.5 text-xs font-semibold tracking-wide backdrop-blur-sm"
				>
					{w.type}
				</span>
				{#if w.delegated}
					<span
						class="rounded-full px-2 py-0.5 text-[0.65rem] font-medium text-[var(--hero-foreground)]/80 ring-1 ring-[var(--hero-foreground)]/25 ring-inset"
					>
						Delegated
					</span>
				{/if}
			</div>
			<p class="mt-2.5 text-xl font-semibold tracking-tight tabular-nums">
				<Money amount={w.value} minorClass="text-[var(--hero-foreground)]/55" />
			</p>
			<p class="mt-0.5 truncate text-sm">
				{#if w.owner}
					<span class="font-medium">{w.owner}</span>
					<span class="text-[var(--hero-foreground)]/55">· {w.name}</span>
				{:else}
					<span class="text-[var(--hero-foreground)]/80">{w.name}</span>
				{/if}
			</p>
		</div>
	</a>
{/snippet}

{#snippet cluster(rows: Wrapper[])}
	<!-- Container-query columns: the cluster keys off the SPAN this block was given, never the
	     viewport. @lg (512px) fits two ~240px chips, @4xl three — the account-cards breakpoints,
	     which these chips share a footprint with. -->
	<div class="grid grid-cols-1 gap-4 @lg:grid-cols-2 @4xl:grid-cols-3">
		{#each rows as w, i (w.href ?? `${w.type}·${w.owner}·${w.name}·${i}`)}
			{@render chip(w)}
		{/each}
	</div>
{/snippet}

<div class="@container flex h-full flex-col" role="group" aria-label="Accounts">
	{#if wrappers.length === 0}
		<EmptyState
			icon={WalletIcon}
			title="Your accounts will appear here"
			description="Once your accounts are open, you’ll see their values and details in this space."
			compact
			chromeless
			class="flex flex-1 flex-col justify-center"
		/>
	{:else if sections === null}
		{@render cluster(wrappers)}
	{:else}
		<div class="space-y-8">
			{#each sections as section (section.label)}
				<section>
					<!-- Group eyebrow on the --card-title-* register (the account-cards idiom): it
					     follows the finish, while the min/max guards keep it an eyebrow — never the
					     full card-title size — under the default card register. -->
					<div class="flex items-baseline justify-between gap-3 px-1">
						<h3
							class="text-muted-foreground min-w-0 truncate font-semibold uppercase"
							style="font-size:min(var(--card-title-size),0.75rem);letter-spacing:max(var(--card-title-tracking),0.08em)"
						>
							{section.label}
						</h3>
						<Money
							amount={section.subtotal}
							class="text-muted-foreground shrink-0 text-sm font-medium"
						/>
					</div>
					<div class="mt-3">
						{@render cluster(section.wrappers)}
					</div>
				</section>
			{/each}
		</div>
	{/if}
</div>
