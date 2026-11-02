<script lang="ts">
	import { cn, type WithElementRef } from '../../../utils.js';
	import type { HTMLAttributes } from 'svelte/elements';

	let {
		ref = $bindable(null),
		class: className,
		children,
		...restProps
	}: WithElementRef<HTMLAttributes<HTMLElement>> = $props();
</script>

<main
	bind:this={ref}
	data-slot="sidebar-inset"
	class={cn(
		// min-w-0: the inset is a flex item, so without it any intrinsically wide
		// descendant (a many-column table) stretches the whole content pane instead
		// of scrolling inside its own overflow container.
		'bg-background md:peer-data-[variant=inset]:m-2 md:peer-data-[variant=inset]:ml-0 md:peer-data-[variant=inset]:rounded-xl md:peer-data-[variant=inset]:shadow-sm md:peer-data-[variant=inset]:peer-data-[state=collapsed]:ml-2 relative flex w-full min-w-0 flex-1 flex-col',
		className
	)}
	{...restProps}
>
	{@render children?.()}
</main>
