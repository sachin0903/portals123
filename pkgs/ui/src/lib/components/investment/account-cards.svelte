<script lang="ts">
	import { accountFace } from '../../categorical.js';
	import { useCardDesigns } from '../../card-designs.js';
	import { Money } from '../ui/money/index.js';
	import { accountStatusChip } from './account-status.js';
	import { changeDirection } from './periods.js';
	import { groupWrappers, isBeneficiary, type GroupAxis } from './grouping.js';
	import { formatGBP, formatPct, formatPctPlain, sanePct } from '../../money.js';
	import { cn } from '../../utils.js';
	import type { Wrapper } from './types.js';

	// The accounts 'cards' variant: the deck, dealt out. A responsive grid of the SAME card the
	// home-page stack shows — the product type's card finish, the type pill, value, owner ·
	// name — so a client who taps a tinted GIA on home finds a tinted GIA on the accounts page, not a
	// flat white tile with the same words. It used to be the white tile, and the accounts page
	// read as a different product from the home page it was reached from. No sparkline: a 7–24
	// card wall of sparklines is noise, not signal, and the record page has the real trace.
	let {
		wrappers,
		groupBy = 'none'
	}: {
		wrappers: Wrapper[];
		/** The block's group_by setting: 'wrapper'/'owner' sections the grid with a quiet header +
		 * subtotal per group; 'none' keeps the flat grid. */
		groupBy?: GroupAxis;
	} = $props();

	// A single group is no grouping: its eyebrow would only repeat the type pill on the one card
	// under it (and the count · total the block's heading already carries). Flat grid instead.
	const sections = $derived.by(() => {
		if (groupBy === 'none') return null;
		const grouped = groupWrappers(wrappers, groupBy);
		return grouped.length > 1 ? grouped : null;
	});

	const cardDesigns = useCardDesigns();
</script>

{#snippet card(w: Wrapper)}
	{@const gainPct = sanePct(w.gainPct)}
	{@const dir = gainPct === null ? null : changeDirection(gainPct)}
	{@const chip = accountStatusChip(w.status)}
	<a
		href={w.href ?? `/accounts/${encodeURIComponent(w.type)}`}
		aria-label="{w.name} — {formatGBP(w.value)}"
		class="hero-surface block rounded-xl p-4 transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 motion-reduce:transition-none motion-reduce:hover:translate-y-0"
		style={accountFace(w.type, cardDesigns())}
	>
		<!-- specular sheen, as on the deck — the glass highlight token, so it dims with the sheen
		     strength a tenant sets rather than always painting a fixed white glint. -->
		<div
			class="pointer-events-none absolute inset-0"
			style="background-image: radial-gradient(120% 80% at 0% 0%, color-mix(in oklch, var(--glass-highlight) 32%, transparent), transparent 58%);"
		></div>
		<div class="relative">
			<div class="flex items-center justify-between gap-3">
				<span class="flex min-w-0 items-center gap-2">
					<span
						class="bg-[var(--hero-foreground)]/15 shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold tracking-wide backdrop-blur-sm"
					>
						{w.type}
					</span>
					{#if w.delegated}
						<span
							class="shrink-0 rounded-full px-2 py-0.5 text-[0.65rem] font-medium text-[var(--hero-foreground)]/80 ring-1 ring-[var(--hero-foreground)]/25 ring-inset"
						>
							Delegated
						</span>
					{/if}
					{#if chip}
						<!-- Legacy /accounts parity: only a NON-active account wears its status
						     (account-status.ts) — the quiet default stays quiet. On the ink face the
						     chip is the same outlined pill as "Delegated"; a status colour would fight
						     the card finish. -->
						<span
							class="shrink-0 rounded-full px-2 py-0.5 text-[0.65rem] font-medium text-[var(--hero-foreground)]/80 ring-1 ring-[var(--hero-foreground)]/25 ring-inset"
						>
							{chip.label}
						</span>
					{/if}
				</span>
				<!-- Neutral-zero + sanePct, matching the stack and the table: a flat account must not
				     read as "up" (+0.0% in green), and an implausible figure renders as an em dash. -->
				<span
					class={cn(
						'shrink-0 text-sm font-medium tabular-nums',
						dir === 'up' && 'text-[var(--hero-positive)]',
						dir === 'down' && 'text-[var(--hero-negative)]',
						(dir === 'flat' || dir === null) && 'text-[var(--hero-foreground)]/60'
					)}
				>
					{dir === null ? '—' : dir === 'flat' ? formatPctPlain(0) : formatPct(gainPct ?? 0)}
				</span>
			</div>
			<p class="mt-3 text-xl font-semibold tracking-tight tabular-nums">
				<Money amount={w.value} minorClass="text-[var(--hero-foreground)]/55" />
			</p>
			{#if isBeneficiary(w)}
				<!-- The family lens: a JISA is the child's money, so the card says so — a small pill
				     replaces the bare owner line, and the child's account reads as the child's. -->
				<p class="mt-1 flex min-w-0 items-center gap-1.5 text-sm">
					<span
						class="bg-[var(--hero-foreground)]/15 shrink-0 rounded-full px-2 py-px text-xs font-medium"
					>
						For {w.owner}
					</span>
					<span class="truncate text-[var(--hero-foreground)]/70">{w.name}</span>
				</p>
			{:else}
				<p class="mt-0.5 truncate text-sm">
					<!-- The separator only earns its place between two names (see the stack). -->
					{#if w.owner}
						<span class="font-medium">{w.owner}</span>
						<span class="text-[var(--hero-foreground)]/55">· {w.name}</span>
					{:else}
						<span class="text-[var(--hero-foreground)]/80">{w.name}</span>
					{/if}
				</p>
			{/if}
		</div>
	</a>
{/snippet}

{#snippet grid(rows: Wrapper[])}
	<div class="grid grid-cols-1 gap-3 @lg:grid-cols-2 @4xl:grid-cols-3">
		{#each rows as w, i (w.href ?? `${w.type}·${w.owner}·${w.name}·${i}`)}
			{@render card(w)}
		{/each}
	</div>
{/snippet}

<!-- Column count keys off the component's CONTAINER width, not the viewport: the old
     sm:/xl: variants rendered a three-across wall of 105px cards when this block sat in a
     narrow grid span on a desktop. @lg (512px) fits two ~240px cards, @4xl (896px) three. -->
<div class="@container" role="group" aria-label="Accounts">
	{#if sections === null}
		{@render grid(wrappers)}
	{:else}
		<div class="space-y-6">
			{#each sections as section (section.label)}
				<section>
					<div class="flex items-baseline justify-between gap-3 px-1">
						<!-- Quiet small-caps section header on the --card-title-* register, so it follows
						     the finish (a tenant/finish re-pointing the title tokens moves this too). The
						     min/max guards keep it an eyebrow under the default card register — the raw
						     title size (1.125rem/tight) would out-shout the cards it labels — while the
						     ledger finish's own eyebrow values (0.75rem/0.08em) pass through unchanged. -->
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
					<div class="mt-2">
						{@render grid(section.wrappers)}
					</div>
				</section>
			{/each}
		</div>
	{/if}
</div>
