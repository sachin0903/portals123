<script lang="ts" module>
	import { type VariantProps, tv } from 'tailwind-variants';

	export const alertVariants = tv({
		base: "grid gap-0.5 rounded-lg border px-2.5 py-2 text-left text-sm has-data-[slot=alert-action]:relative has-data-[slot=alert-action]:pr-18 has-[>svg]:grid-cols-[auto_1fr] has-[>svg]:gap-x-2 *:[svg]:row-span-2 *:[svg]:translate-y-0.5 *:[svg]:text-current *:[svg:not([class*='size-'])]:size-4 group/alert relative w-full",
		variants: {
			/* Each status variant is the derived tonal trio (subtle surface / strong ink / line
			   hairline) from theme.css — see status-badge/tones.ts for the canonical map. The base
			   tokens on their own were the wrong half of the idiom: `text-success` is a mid-lightness
			   saturated hue, so it sat at ~3.4:1 against the card it was printed on. `-strong` mixes
			   the same token toward --foreground, which is readable in light AND dark and still
			   re-themes when a firm changes --success. */
			variant: {
				default: 'bg-card text-card-foreground',
				destructive:
					'text-destructive-strong bg-destructive-subtle border-destructive-line *:data-[slot=alert-description]:text-destructive-strong/90 *:[svg]:text-current',
				// Graphene additions — kept across `shadcn-svelte add alert --overwrite` by re-applying.
				success:
					'text-success-strong bg-success-subtle border-success-line *:data-[slot=alert-description]:text-success-strong/90 *:[svg]:text-current',
				warning:
					'text-warning-strong bg-warning-subtle border-warning-line *:data-[slot=alert-description]:text-warning-strong/90 *:[svg]:text-current',
				info: 'text-info-strong bg-info-subtle border-info-line *:data-[slot=alert-description]:text-info-strong/90 *:[svg]:text-current'
			}
		},
		defaultVariants: {
			variant: 'default'
		}
	});

	export type AlertVariant = VariantProps<typeof alertVariants>['variant'];
</script>

<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn, type WithElementRef } from '../../../utils.js';

	let {
		ref = $bindable(null),
		class: className,
		variant = 'default',
		children,
		...restProps
	}: WithElementRef<HTMLAttributes<HTMLDivElement>> & {
		variant?: AlertVariant;
	} = $props();
</script>

<div
	bind:this={ref}
	data-slot="alert"
	role="alert"
	class={cn(alertVariants({ variant }), className)}
	{...restProps}
>
	{@render children?.()}
</div>
