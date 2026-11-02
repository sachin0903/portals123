<script lang="ts">
	import { Dialog as DialogPrimitive } from 'bits-ui';
	import { cn } from '../../../utils.js';

	let {
		ref = $bindable(null),
		class: className,
		...restProps
	}: DialogPrimitive.OverlayProps = $props();
</script>

<DialogPrimitive.Overlay
	bind:ref
	data-slot="dialog-overlay"
	class={cn(
		// The scrim is the INK ANCHOR at low alpha, not a literal black wash: a scrim has to be dark in
		// BOTH schemes, so --foreground is wrong (it inverts to near-white) and a fixed colour ignores
		// the firm's ink entirely. It also has to be heavier on a dark canvas — 10% of anything over
		// oklch(0.18) barely reads as a scrim at all, which is why the dark step is here.
		'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 bg-[color-mix(in_oklch,var(--hero-base)_20%,transparent)] dark:bg-[color-mix(in_oklch,var(--hero-base)_48%,transparent)] duration-100 supports-backdrop-filter:backdrop-blur-xs fixed inset-0 isolate z-50',
		className
	)}
	{...restProps}
/>
