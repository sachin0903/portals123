<script lang="ts">
	import { Button, Input, Label, Select } from '@graphene/ui';
	import { STATUSES, STATUS_LABEL } from '$lib/transfers/status';
	import type { TransfersStore } from '$lib/transfers/store.svelte';
	import type { TransferStatus } from '$lib/transfers/types';

	let { wb }: { wb: TransfersStore } = $props();

	const all: TransferStatus[] = [...STATUSES, 'manual_intervention'];
	let to = $state<TransferStatus>('reconciled');
	let reason = $state('');

	function apply() {
		wb.forceStep(to, reason);
		reason = '';
	}
</script>

<div class="space-y-2">
	<div class="grid grid-cols-2 gap-2">
		<div class="space-y-1">
			<Label class="text-[11px]">Force to status</Label>
			<Select.Root type="single" bind:value={to}>
				<Select.Trigger class="w-full">{STATUS_LABEL[to]}</Select.Trigger>
				<Select.Content>
					{#each all as s (s)}
						<Select.Item value={s} label={STATUS_LABEL[s]} />
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
		<div class="space-y-1">
			<Label class="text-[11px]">Reason (audit)</Label>
			<Input bind:value={reason} placeholder="why…" />
		</div>
	</div>
	<Button variant="outline" size="sm" class="w-full" onclick={apply}>Apply force-step</Button>
</div>
