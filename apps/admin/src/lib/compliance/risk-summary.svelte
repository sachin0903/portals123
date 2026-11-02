<script lang="ts">
	// ComplyCube's risk profile for the client: the overall level and what it rests on, one row per
	// factor. Operations only — a rating can say a client is politically exposed or on a watchlist.
	import { StatusBadge, countryName } from '@graphene/ui';
	import { riskOf, shortDate, type AmlRisk } from '$lib/aml';

	let { risk }: { risk: AmlRisk | null } = $props();

	const overall = $derived(riskOf(risk?.overall ?? null));
</script>

<section class="min-w-0 space-y-2">
	<div class="flex items-center justify-between gap-2">
		<h2 class="text-muted-foreground text-[0.7rem] font-semibold tracking-wide uppercase">
			ComplyCube risk
		</h2>
		{#if risk}<StatusBadge tone={overall.tone}>{overall.label}</StatusBadge>{/if}
	</div>
	{#if !risk}
		<p class="text-muted-foreground">Not fetched yet.</p>
	{:else if !risk.factors.length}
		<p class="text-muted-foreground">ComplyCube gave no factors.</p>
	{:else}
		<ul class="divide-border divide-y">
			{#each risk.factors as factor (factor.key)}
				{@const level = riskOf(factor.risk)}
				{@const detail = factor.key === 'country' ? countryName(factor.detail) : factor.detail}
				<li class="flex items-center justify-between gap-3 py-1.5">
					<span class="min-w-0">
						<span class="block">{factor.label}</span>
						{#if detail}
							<span class="text-muted-foreground block truncate text-xs" title={detail}
								>{detail}</span
							>
						{/if}
					</span>
					<StatusBadge tone={level.tone}>{level.label}</StatusBadge>
				</li>
			{/each}
		</ul>
	{/if}
	{#if risk}
		<p class="text-muted-foreground text-xs">Updated {shortDate(risk.updated_at)}</p>
	{/if}
</section>
