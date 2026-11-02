<script lang="ts">
	import { Alert, AlertDescription, Button } from '@graphene/ui';
	import { STATUS_LABEL } from '$lib/contributions/status';
	import type {
		ContributionFrequency,
		ContributionSource,
		ContributionType
	} from '$lib/contributions/types';
	import type { WorkbenchStore } from '$lib/contributions/store.svelte';
	import CategoriseFields from './CategoriseFields.svelte';
	import GrossUp from './GrossUp.svelte';
	import StatusBadge from './StatusBadge.svelte';

	let { wb }: { wb: WorkbenchStore } = $props();

	let ftype = $state<ContributionType>('personal');
	let ffreq = $state<ContributionFrequency>('one_off');
	let fsource = $state<ContributionSource>('Client');
	let ftaxYear = $state('2026/27');

	$effect(() => {
		const c = wb.selected;
		if (c) {
			ftype = c.type;
			ffreq = c.freq;
			fsource = c.source;
			ftaxYear = c.taxYear;
		}
	});

	const preview = $derived(wb.selected ? { ...wb.selected, type: ftype, freq: ffreq } : null);
	const recordable = $derived(
		!!wb.selected &&
			['identified', 'awaiting_confirmation', 'confirmed'].includes(wb.selected.status)
	);
</script>

<div class="border-border/70 bg-card flex h-full flex-col rounded-xl border">
	<div class="border-border/70 flex items-center justify-between border-b px-4 py-3">
		<h3 class="font-semibold">Classify &amp; record</h3>
		{#if wb.selected}
			<StatusBadge status={wb.selected.status} />
		{/if}
	</div>

	{#if !wb.selected}
		<div
			class="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-2 p-10 text-center text-sm"
		>
			<p>Select a contribution from the queue to classify it and record it to Morgan Lloyd.</p>
		</div>
	{:else}
		{@const c = wb.selected}
		<div class="space-y-4 p-4">
			<div>
				<div class="text-lg font-semibold">{c.member}</div>
				<div
					class="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px]"
				>
					<span>SEI a/c {c.seiAccount}</span>
					<span>Scheme {c.scheme}</span>
					<span>Received {c.date}</span>
				</div>
			</div>

			<div>
				<h4 class="text-muted-foreground mb-2 text-xs font-semibold">Classification</h4>
				<CategoriseFields
					bind:type={ftype}
					bind:freq={ffreq}
					bind:source={fsource}
					bind:taxYear={ftaxYear}
				/>
			</div>

			<div>
				<h4 class="text-muted-foreground mb-2 text-xs font-semibold">Relief-at-source gross-up</h4>
				{#if preview}<GrossUp contribution={preview} />{/if}
			</div>

			<div class="flex flex-col gap-2 pt-1">
				{#if recordable}
					<Button
						onclick={() =>
							wb.categorise(c.id, { type: ftype, freq: ffreq, source: fsource, taxYear: ftaxYear })}
					>
						Save categorisation
					</Button>
					<Button variant="secondary" disabled={!c.categorised} onclick={() => wb.recordToMl(c.id)}>
						Confirm & record to Morgan Lloyd
					</Button>
					<div class="flex gap-2">
						<Button variant="outline" class="flex-1" onclick={() => wb.hold(c.id)}>Hold</Button>
						<Button
							variant="outline"
							class="text-destructive flex-1"
							onclick={() => wb.flagManual(c.id)}>Flag for manual</Button
						>
					</div>
				{:else}
					<Alert>
						<AlertDescription>
							This contribution is at <strong>{STATUS_LABEL[c.status]}</strong> and can no longer be classified.
							Track its relief on the RAS &amp; tax relief tab.
						</AlertDescription>
					</Alert>
				{/if}
			</div>
		</div>
	{/if}
</div>
