<script lang="ts">
	import { Button, Input, Label, Select } from '@graphene/ui';
	import type { TransfersStore } from '$lib/transfers/store.svelte';
	import type { BreakStatus } from '$lib/transfers/types';

	let { wb }: { wb: TransfersStore } = $props();

	const opts: { value: BreakStatus; label: string }[] = [
		{ value: 'investigating', label: 'Investigating' },
		{ value: 'resolved', label: 'Resolved' },
		{ value: 'accepted', label: 'Accepted (rounding)' }
	];
	let status = $state<BreakStatus>('investigating');
	let note = $state('');
	const label = $derived(opts.find((o) => o.value === status)?.label ?? status);

	function apply() {
		wb.resolveBreak(status, note);
		note = '';
	}
</script>

<div class="border-destructive-line bg-destructive-subtle space-y-2 rounded-lg border p-3">
	<div class="text-destructive-strong text-xs font-semibold">Resolve reconciliation break</div>
	<div class="grid grid-cols-2 gap-2">
		<div class="space-y-1">
			<Label class="text-[11px]">Outcome</Label>
			<Select.Root type="single" bind:value={status}>
				<Select.Trigger class="w-full">{label}</Select.Trigger>
				<Select.Content>
					{#each opts as o (o.value)}
						<Select.Item value={o.value} label={o.label} />
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
		<div class="space-y-1">
			<Label class="text-[11px]">Note</Label>
			<Input bind:value={note} placeholder="audit note…" />
		</div>
	</div>
	<Button size="sm" class="w-full" onclick={apply}>Apply</Button>
</div>
