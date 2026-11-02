<script lang="ts">
	import type { Transfer } from '$lib/transfers/types';

	let { transfer }: { transfer: Transfer } = $props();

	type Leg = { role: string; name: string; rows: [string, string][] };
	const legs = $derived.by<Leg[]>(() => {
		const out: Leg[] = [
			{
				role: 'Ceding provider',
				name: transfer.provider,
				rows: transfer.plan ? [['Plan', transfer.plan]] : []
			}
		];
		if (transfer.route === 'ml_sei' && transfer.ml) {
			out.push({
				role: 'Scheme admin · Morgan Lloyd',
				name: transfer.ml.scheme ?? '—',
				rows: [
					['Transfer ref', transfer.ml.ref ?? '—'],
					['Settlement', transfer.ml.settle ?? 'not advised']
				]
			});
		}
		out.push({
			role: `Custody · ${transfer.custodian === 'seccl' ? 'Seccl' : 'SEI'}`,
			name: transfer.custody.acct ?? '—',
			rows: [
				['Ref', transfer.custody.ref ?? '—'],
				['Status', transfer.custodyStatus ?? transfer.cedingProgress ?? '—']
			]
		});
		return out;
	});
</script>

<div class="space-y-2">
	{#each legs as leg, i (leg.role)}
		<div class="border-border/70 bg-muted/30 rounded-lg border p-3">
			<div class="text-muted-foreground text-[11px] font-semibold">
				{leg.role}
			</div>
			<div class="font-medium">{leg.name}</div>
			{#each leg.rows as [k, v] (k)}
				<div class="text-muted-foreground mt-0.5 flex justify-between font-mono text-[11px]">
					<span>{k}</span><span class="text-foreground">{v}</span>
				</div>
			{/each}
		</div>
		{#if i < legs.length - 1}
			<div class="text-muted-foreground/50 pl-3 text-xs">↓</div>
		{/if}
	{/each}
</div>
