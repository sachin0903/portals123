<script lang="ts">
	// The transfer saga as a row of clickable counters — the same control the contributions
	// workbench puts across the top, so the two surfaces read alike.
	//
	// Each stage names its ACTOR underneath. Most of a transfer is waiting on somebody else (the
	// ceding scheme, the scheme administrator), and knowing which stages are not yours to chase
	// is most of the value of the strip.
	//
	// A synced row takes the stage its own status maps to. A completed stock migration is
	// Completed (`funds_sent`); it is not the reconciled Complete.
	import { cn } from '../../../utils.js';
	import { OFF_SAGA, SAGA, type ApiTransfer, type ApiTransferStatus } from './api.js';

	let {
		transfers,
		active = $bindable(null)
	}: {
		transfers: ApiTransfer[];
		/** The selected stage's status, or null for "all". */
		active?: ApiTransferStatus | null;
	} = $props();

	const countFor = (status: ApiTransferStatus) =>
		transfers.filter((t) => t.status === status).length;

	/** Off-saga rows get their own counter at the end rather than a place on the rail — they are
	 *  ways a transfer stopped, not stages it passed through. */
	const stopped = $derived(transfers.filter((t) => OFF_SAGA.has(t.status)));

	function toggle(status: ApiTransferStatus | null) {
		active = active === status ? null : status;
	}
</script>

<div
	class="border-border/70 bg-card flex items-stretch gap-0 overflow-x-auto rounded-xl border p-1.5"
>
	{#each SAGA as stage, i (stage.status)}
		<button
			type="button"
			onclick={() => toggle(stage.status)}
			aria-pressed={active === stage.status}
			class={cn(
				'relative min-w-[7rem] flex-1 rounded-lg px-3 py-2 text-left transition-colors',
				active === stage.status ? 'bg-brand/10 ring-brand/40 ring-1' : 'hover:bg-muted/60'
			)}
		>
			{#if i > 0}
				<span
					aria-hidden="true"
					class="text-muted-foreground/50 absolute top-1/2 -left-0.5 -translate-y-1/2 text-xs"
				>
					→
				</span>
			{/if}
			<div
				class={cn(
					'text-xl font-semibold tracking-tight tabular-nums',
					stage.status === 'completed' ? 'text-success-strong' : 'text-foreground'
				)}
			>
				{countFor(stage.status)}
			</div>
			<div class="text-muted-foreground mt-0.5 text-[11.5px] font-semibold">
				{stage.label}
			</div>
			<div class="text-muted-foreground/70 text-[9.5px] font-medium">{stage.actor}</div>
		</button>
	{/each}

	{#if stopped.length > 0}
		<button
			type="button"
			onclick={() => toggle('manual_intervention')}
			aria-pressed={active === 'manual_intervention'}
			class={cn(
				'min-w-[7rem] flex-1 rounded-lg px-3 py-2 text-left transition-colors',
				active === 'manual_intervention' ? 'bg-brand/10 ring-brand/40 ring-1' : 'hover:bg-muted/60'
			)}
		>
			<div class="text-destructive-strong text-xl font-semibold tracking-tight tabular-nums">
				{transfers.filter((t) => t.status === 'manual_intervention').length}
			</div>
			<div class="text-muted-foreground mt-0.5 text-[11.5px] font-semibold">Needs attention</div>
			<div class="text-muted-foreground/70 text-[9.5px] font-medium">Graphene</div>
		</button>
	{/if}
</div>
