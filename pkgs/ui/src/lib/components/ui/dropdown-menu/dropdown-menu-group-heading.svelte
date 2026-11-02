<script lang="ts">
	// MUST BE INSIDE A `DropdownMenu.Group`. bits-ui reads its labelling context from one and THROWS
	// without it — "Context Menu.Group | Menu.RadioGroup not found" — which kills the whole Content
	// render: the trigger reports `data-state="open"` and nothing appears. Nothing in the types says so
	// and svelte-check passes either way, so it is only ever found by opening the menu.
	import { DropdownMenu as DropdownMenuPrimitive } from 'bits-ui';
	import { cn } from '../../../utils.js';
	import type { ComponentProps } from 'svelte';

	let {
		ref = $bindable(null),
		class: className,
		inset,
		...restProps
	}: ComponentProps<typeof DropdownMenuPrimitive.GroupHeading> & {
		inset?: boolean;
	} = $props();
</script>

<DropdownMenuPrimitive.GroupHeading
	bind:ref
	data-slot="dropdown-menu-group-heading"
	data-inset={inset}
	class={cn('px-2 py-1.5 text-sm font-semibold data-[inset]:ps-8', className)}
	{...restProps}
/>
