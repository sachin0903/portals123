<script lang="ts">
	// A range slider: two handles over one track, sitting above (not replacing) a From/To pair of
	// boxes. `type="multiple"` is bits-ui's two-handle mode. Handles are not reordered when they
	// cross — the filter caps From at To, and a swap would raise To instead.
	import { Slider as SliderPrimitive } from 'bits-ui';
	import { cn } from '../../../utils.js';

	let {
		value = $bindable([0, 100]),
		min = 0,
		max = 100,
		step = 1,
		label,
		disabled = false,
		onValueChange,
		class: className
	}: {
		value?: number[];
		min?: number;
		max?: number;
		step?: number;
		label: string;
		disabled?: boolean;
		onValueChange?: (next: number[]) => void;
		class?: string;
	} = $props();
</script>

<SliderPrimitive.Root
	type="multiple"
	bind:value
	{min}
	{max}
	{step}
	{disabled}
	{onValueChange}
	autoSort={false}
	data-slot="slider"
	class={cn('relative flex w-full touch-none items-center select-none', className)}
>
	{#snippet children({ thumbItems })}
		<span class="bg-muted relative h-1.5 w-full grow overflow-hidden rounded-full">
			<SliderPrimitive.Range class="bg-primary absolute h-full" />
		</span>
		{#each thumbItems as thumb (thumb.index)}
			<!-- `border-background`: a ring of the panel's colour keeps the two handles distinguishable where they overlap. -->
			<SliderPrimitive.Thumb
				index={thumb.index}
				aria-label="{label} {thumb.index === 0 ? 'minimum' : 'maximum'}"
				class="border-background bg-primary focus-visible:ring-ring/50 block size-4 shrink-0 rounded-full border-2 shadow-sm transition-colors focus-visible:ring-3 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
			/>
		{/each}
	{/snippet}
</SliderPrimitive.Root>
