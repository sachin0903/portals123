<script lang="ts">
	// Same surface as `DropdownMenu.Content` — one popover look across the product — with one deliberate
	// difference: NO `w-(--bits-context-menu-anchor-width)`. A context menu's anchor is the zero-size
	// virtual element at the pointer, so sizing to it would render a menu 0px wide. `w-auto` and the
	// items' own padding decide the width instead.
	import { cn } from '../../../utils.js';
	import { ContextMenu as ContextMenuPrimitive } from 'bits-ui';

	let {
		ref = $bindable(null),
		class: className,
		...restProps
	}: ContextMenuPrimitive.ContentProps = $props();
</script>

<ContextMenuPrimitive.Portal>
	<ContextMenuPrimitive.Content
		bind:ref
		data-slot="context-menu-content"
		class={cn(
			'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 ring-foreground/10 bg-popover text-popover-foreground z-50 max-h-(--bits-context-menu-content-available-height) w-auto min-w-40 overflow-x-hidden overflow-y-auto rounded-lg p-1 shadow-md ring-1 duration-100 outline-none',
			className
		)}
		{...restProps}
	/>
</ContextMenuPrimitive.Portal>
