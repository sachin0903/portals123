<script lang="ts" module>
	export type Side = 'top' | 'right' | 'bottom' | 'left';
</script>

<script lang="ts">
	import { Dialog as SheetPrimitive } from 'bits-ui';
	import type { Snippet } from 'svelte';
	import SheetPortal from './sheet-portal.svelte';
	import SheetOverlay from './sheet-overlay.svelte';
	import { Button } from '../button/index.js';
	import XIcon from '@lucide/svelte/icons/x';
	import { cn, type WithoutChildrenOrChild } from '../../../utils.js';
	import type { ComponentProps } from 'svelte';

	let {
		ref = $bindable(null),
		class: className,
		side = 'right',
		showCloseButton = true,
		portalProps,
		children,
		...restProps
	}: WithoutChildrenOrChild<SheetPrimitive.ContentProps> & {
		portalProps?: WithoutChildrenOrChild<ComponentProps<typeof SheetPortal>>;
		side?: Side;
		showCloseButton?: boolean;
		children: Snippet;
	} = $props();
</script>

<SheetPortal {...portalProps}>
	<SheetOverlay />
	<SheetPrimitive.Content
		bind:ref
		data-slot="sheet-content"
		data-side={side}
		class={cn(
			// Left/right drawers are FULL WIDTH below `sm` (a phone has no room for a 25% peek)
			// and `sm:w-[58vw]` from there — the SIPP illustration drawer. Form drawers
			// (payment, withdraw, ad hoc fee, contribution, transfer, vulnerability, drawdown
			// hand-off) pass `sm:max-w-xl` (the previous wide).
			// Do not put a 24rem one-field-column cap or `w-3/4` (the shadcn default, a peek on a
			// phone) back. A console that needs MORE uses `sm:!w-[…vw]`; a peek (mobile nav, a
			// document rail) opts out with `sm:!w-*`. Bottom/top stay edge-to-edge.
			'bg-popover text-popover-foreground fixed z-50 flex flex-col gap-4 overflow-hidden bg-clip-padding text-sm shadow-lg transition ease-in-out data-[state=open]:duration-200 data-[state=closed]:duration-200 data-[side=bottom]:inset-x-0 data-[side=bottom]:bottom-0 data-[side=bottom]:h-auto data-[side=bottom]:border-t data-[side=left]:inset-y-0 data-[side=left]:left-0 data-[side=left]:h-full data-[side=left]:w-full data-[side=left]:overflow-hidden data-[side=left]:border-r data-[side=right]:inset-y-0 data-[side=right]:right-0 data-[side=right]:h-full data-[side=right]:w-full data-[side=right]:overflow-hidden data-[side=right]:border-l data-[side=top]:inset-x-0 data-[side=top]:top-0 data-[side=top]:h-auto data-[side=top]:border-b data-[side=left]:sm:w-[58vw] data-[side=right]:sm:w-[58vw] data-[side=bottom]:sm:max-w-none data-[side=top]:sm:max-w-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[side=bottom]:data-[state=open]:slide-in-from-bottom-[100%] data-[side=left]:data-[state=open]:slide-in-from-left-[100%] data-[side=right]:data-[state=open]:slide-in-from-right-[100%] data-[side=top]:data-[state=open]:slide-in-from-top-[100%] data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[side=bottom]:data-[state=closed]:slide-out-to-bottom-[100%] data-[side=left]:data-[state=closed]:slide-out-to-left-[100%] data-[side=right]:data-[state=closed]:slide-out-to-right-[100%] data-[side=top]:data-[state=closed]:slide-out-to-top-[100%]',
			className
		)}
		{...restProps}
	>
		{@render children?.()}
		{#if showCloseButton}
			<SheetPrimitive.Close data-slot="sheet-close">
				{#snippet child({ props })}
					<Button variant="ghost" class="absolute top-3 right-3" size="icon-sm" {...props}>
						<XIcon />
						<span class="sr-only">Close</span>
					</Button>
				{/snippet}
			</SheetPrimitive.Close>
		{/if}
	</SheetPrimitive.Content>
</SheetPortal>
