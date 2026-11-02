<script lang="ts">
	import { Card, cn, formatGBP } from '@graphene/ui';
	import { RAS_BATCHES } from '$lib/contributions/data';
	import StatusBadge from './StatusBadge.svelte';

	// The tick's ink travels with its dot fill, so each state owns its own foreground.
	const dot = {
		done: 'bg-success border-success text-success-foreground',
		cur: 'bg-brand border-brand text-brand-foreground',
		wait: 'border-border bg-background text-foreground'
	} as const;
</script>

<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
	{#each RAS_BATCHES as batch (batch.period)}
		<!-- The current-batch accent is inline because `.glass-panel` sets the `border` shorthand
		     after Tailwind's utilities, which flattens `border-t-4 border-t-brand` to the hairline. -->
		<Card
			glass
			class="p-4"
			style={batch.current ? 'border-top: 4px solid var(--brand)' : undefined}
		>
			<div class="flex items-center justify-between">
				<span class="font-semibold">{batch.period}</span>
				<StatusBadge status={batch.state} />
			</div>
			<div class="mt-3 flex gap-6">
				<div>
					<div class="text-muted-foreground text-[11px] font-semibold">Personal conts</div>
					<div class="text-xl font-semibold tabular-nums">{batch.count}</div>
				</div>
				<div>
					<div class="text-muted-foreground text-[11px] font-semibold">
						Relief {batch.accruing ? 'accruing' : 'claimed'}
					</div>
					<div class="text-brand-strong text-xl font-semibold tabular-nums">
						{formatGBP(batch.relief)}
					</div>
				</div>
			</div>
			<ol class="mt-4 space-y-0">
				{#each batch.steps as step, i (step.title)}
					<li class="relative flex gap-3 pb-3 last:pb-0">
						{#if i < batch.steps.length - 1}
							<span
								class={cn(
									'absolute top-4 left-[7px] h-full w-px',
									step.state === 'done' ? 'bg-success-line' : 'bg-border'
								)}
							></span>
						{/if}
						<span
							class={cn(
								'mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full border-2 text-[8px]',
								dot[step.state]
							)}>{step.state === 'done' ? '✓' : ''}</span
						>
						<div class="flex-1">
							<div class={cn('text-xs font-medium', step.state === 'cur' && 'text-brand-strong')}>
								{step.title}
							</div>
							{#if step.meta}<div class="text-muted-foreground text-[10.5px]">{step.meta}</div>{/if}
						</div>
					</li>
				{/each}
			</ol>
		</Card>
	{/each}
</div>
