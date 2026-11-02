<script lang="ts">
	// The transfer's progress, on the REAL saga, stacked so a 420px panel does not scroll sideways.
	// The custodian's own status sits in the detail header, not under this list. The flow ends
	// when the custodian has the assets. Reconciliation is a section, not a further stage.
	import { cn } from '../../../utils.js';
	import { OFF_SAGA, sagaIndex, type ApiTransfer } from './api.js';
	import { cancellationRequested, railStages } from './stages.js';

	let { transfer }: { transfer: ApiTransfer } = $props();

	const stages = $derived(railStages(transfer.route));
	const pendingCancel = $derived(cancellationRequested(transfer));
	const stopped = $derived(OFF_SAGA.has(transfer.status));
	const current = $derived(sagaIndex(transfer.status));
</script>

<div class="space-y-3">
	<ol class="flex flex-col">
		{#each stages as stage, i (stage.status)}
			{@const index = sagaIndex(stage.status)}
			{@const done =
				!stopped && current > -1 && (pendingCancel ? index <= current : index < current)}
			{@const here = !stopped && !pendingCancel && stage.status === transfer.status}
			{@const cancelHere = pendingCancel && stage.status === transfer.status}
			{@const last = i === stages.length - 1 && !cancelHere && !stopped}
			<li class="flex gap-2">
				<div class="flex w-3 flex-col items-center">
					<span
						class={cn(
							'mt-1.5 size-1.5 shrink-0 rounded-full',
							done ? 'bg-success' : here ? 'bg-info' : 'bg-muted-foreground/40'
						)}
					></span>
					{#if !last}
						<span class="bg-muted-foreground/40 w-px flex-1"></span>
					{/if}
				</div>
				<div class={cn('flex min-w-0 items-baseline gap-2', !last && 'pb-3')}>
					<span
						class={cn(
							'text-xs font-semibold',
							here ? 'text-info-strong' : done ? 'text-foreground' : 'text-muted-foreground'
						)}
					>
						{stage.label}
					</span>
					<span class="text-muted-foreground text-[10.5px]">{stage.actor}</span>
				</div>
			</li>
			{#if cancelHere}
				{@const cancelLast = i === stages.length - 1}
				<li class="flex gap-2">
					<div class="flex w-3 flex-col items-center">
						<span class="bg-info mt-1.5 size-1.5 shrink-0 rounded-full"></span>
						{#if !cancelLast}
							<span class="bg-muted-foreground/40 w-px flex-1"></span>
						{/if}
					</div>
					<div class={cn('flex min-w-0 items-baseline gap-2', !cancelLast && 'pb-3')}>
						<span class="text-info-strong text-xs font-semibold">Cancellation requested</span>
						<span class="text-muted-foreground text-[10.5px]">Seccl</span>
					</div>
				</li>
			{/if}
		{/each}
		{#if stopped}
			<li class="flex gap-2">
				<div class="flex w-3 flex-col items-center">
					<span
						class={cn(
							'mt-1.5 size-1.5 shrink-0 rounded-full',
							transfer.status === 'cancelled' ? 'bg-muted-foreground/60' : 'bg-destructive'
						)}
					></span>
				</div>
				<div class="flex min-w-0 items-baseline gap-2">
					<span
						class={cn(
							'text-xs font-semibold',
							transfer.status === 'cancelled' ? 'text-muted-foreground' : 'text-destructive-strong'
						)}
					>
						{transfer.status === 'cancelled' ? 'Cancelled' : 'Needs attention'}
					</span>
					<span class="text-muted-foreground text-[10.5px]">Graphene</span>
				</div>
			</li>
		{/if}
	</ol>
</div>
