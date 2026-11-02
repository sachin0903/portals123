<script lang="ts">
	import { Select as SelectPrimitive } from 'bits-ui';
	import { cn, type WithoutChild } from '../../../utils.js';
	import CheckIcon from '@lucide/svelte/icons/check';

	let {
		ref = $bindable(null),
		class: className,
		value,
		label,
		children: childrenProp,
		...restProps
	}: WithoutChild<SelectPrimitive.ItemProps> = $props();
</script>

<SelectPrimitive.Item
	bind:ref
	{value}
	data-slot="select-item"
	class={cn(
		"focus:bg-accent focus:text-accent-foreground not-data-[variant=destructive]:focus:**:text-accent-foreground gap-1.5 rounded-md py-1 pr-8 pl-1.5 text-sm [&_svg:not([class*='size-'])]:size-4 *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2 focus:bg-accent data-highlighted:bg-accent data-highlighted:text-accent-foreground focus:text-accent-foreground relative flex w-full cursor-default items-center outline-hidden select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
		className
	)}
	{...restProps}
>
	{#snippet children({ selected, highlighted })}
		<span class="absolute end-2 flex size-3.5 items-center justify-center">
			{#if selected}
				<CheckIcon class="cn-select-item-indicator-icon" />
			{/if}
		</span>
		<!-- `min-w-0` and a truncating inner span, where this was `shrink-0 whitespace-nowrap`: an
		     unshrinkable nowrap item makes the POPUP as wide as its longest option, and the popup has
		     no width of its own to push back with. A firm whose investment models are named
		     "Balanced Growth (Sustainable, Higher Risk)" got a menu wider than the form. The `title`
		     keeps a clipped option readable without opening it in a wider window. -->
		<span class="flex min-w-0 flex-1 gap-2">
			{#if childrenProp}
				{@render childrenProp({ selected, highlighted })}
			{:else}
				<span class="truncate" title={label || String(value ?? '')}>{label || value}</span>
			{/if}
		</span>
	{/snippet}
</SelectPrimitive.Item>
