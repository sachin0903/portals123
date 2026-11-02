<script lang="ts">
	import { Alert, AlertDescription, cn, formatGBP } from '@graphene/ui';
	import type { Transfer } from '$lib/transfers/types';

	let { transfer }: { transfer: Transfer } = $props();

	const recon = $derived(transfer.recon);
	const custody = $derived(recon ? (recon.legs.at(-1)?.value ?? recon.expected) : 0);
	const delta = $derived(recon ? custody - recon.expected : 0);
	const within = $derived(Math.abs(delta) <= 0.01);
</script>

{#if recon}
	<div class="border-border/70 overflow-hidden rounded-lg border">
		<div class="grid" style="grid-template-columns: repeat({recon.legs.length}, 1fr)">
			{#each recon.legs as leg (leg.key)}
				<div class="border-border/70 border-r p-3 text-center last:border-r-0">
					<div class="text-muted-foreground text-[11px] font-semibold">
						{leg.label}
					</div>
					<div class="mt-1 font-mono text-sm font-semibold tabular-nums">
						{formatGBP(leg.value)}
					</div>
				</div>
			{/each}
		</div>
		<div class="border-border/70 bg-muted/30 flex items-center justify-between border-t px-3 py-2">
			<span class="text-muted-foreground text-xs"
				>Δ custody − expected: <span
					class={cn('font-mono', within ? 'text-success-strong' : 'text-destructive-strong')}
					>{delta >= 0 ? '+' : '−'}{formatGBP(Math.abs(delta))}</span
				></span
			>
			<span
				class={cn(
					'text-xs font-semibold',
					within ? 'text-success-strong' : 'text-destructive-strong'
				)}
			>
				{within ? 'Within tolerance ±£0.01' : 'Out of tolerance'}
			</span>
		</div>
	</div>
{:else}
	<Alert>
		<AlertDescription class="text-xs">
			Reconciliation runs once the custody credit is matched against the advised value ({transfer.route ===
			'ml_sei'
				? '3-way: Graphene / Morgan Lloyd / SEI'
				: '2-way'}).
		</AlertDescription>
	</Alert>
{/if}
