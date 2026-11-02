<script lang="ts">
	import { Dialog as SheetPrimitive } from 'bits-ui';
	import { cn } from '../../../utils.js';

	let {
		ref = $bindable(null),
		class: className,
		...restProps
	}: SheetPrimitive.OverlayProps = $props();
</script>

<SheetPrimitive.Overlay
	bind:ref
	data-slot="sheet-overlay"
	class={cn(
		// Same scrim as the dialog overlay, and for the same reasons (see dialog-overlay.svelte):
		// the ink anchor is the one token that stays dark in both schemes, and a 10% wash is
		// invisible on a dark canvas.
		'bg-[color-mix(in_oklch,var(--hero-base)_20%,transparent)] dark:bg-[color-mix(in_oklch,var(--hero-base)_48%,transparent)] supports-backdrop-filter:backdrop-blur-xs fixed inset-0 z-50',
		className
	)}
	{...restProps}
/>
