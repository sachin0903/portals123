<script lang="ts">
	import { Label, Segmented, Select } from '@graphene/ui';
	import type {
		ContributionFrequency,
		ContributionSource,
		ContributionType
	} from '$lib/contributions/types';

	let {
		type = $bindable(),
		freq = $bindable(),
		source = $bindable(),
		taxYear = $bindable()
	}: {
		type: ContributionType;
		freq: ContributionFrequency;
		source: ContributionSource;
		taxYear: string;
	} = $props();

	const sources: ContributionSource[] = ['Client', 'Third party', 'Employer'];
	const taxYears = ['2026/27', '2025/26'];
</script>

<div class="space-y-3">
	<div class="space-y-1.5">
		<Label>Contribution type</Label>
		<Segmented
			bind:value={type}
			options={[
				{ value: 'personal', label: 'Personal · Net' },
				{ value: 'employer', label: 'Employer · Gross' }
			]}
		/>
	</div>
	<div class="space-y-1.5">
		<Label>Frequency</Label>
		<Segmented
			bind:value={freq}
			options={[
				{ value: 'one_off', label: 'Single' },
				{ value: 'regular', label: 'Regular' }
			]}
		/>
	</div>
	<div class="grid grid-cols-2 gap-3">
		<div class="space-y-1.5">
			<Label>Paid by</Label>
			<Select.Root type="single" bind:value={source}>
				<Select.Trigger class="w-full">{source}</Select.Trigger>
				<Select.Content>
					{#each sources as s (s)}
						<Select.Item value={s} label={s} />
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
		<div class="space-y-1.5">
			<Label>Tax year</Label>
			<Select.Root type="single" bind:value={taxYear}>
				<Select.Trigger class="w-full">{taxYear}</Select.Trigger>
				<Select.Content>
					{#each taxYears as y (y)}
						<Select.Item value={y} label={y} />
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
	</div>
</div>
