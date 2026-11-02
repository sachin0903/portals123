<script lang="ts" module>
	import { type VariantProps, tv } from 'tailwind-variants';

	export const badgeVariants = tv({
		// `rounded-full` (not Tailwind's fixed 4xl step = 2rem, which bypasses --radius): a badge
		// is a pill, and at h-5 the two are pixel-identical anyway.
		//
		// `max-w-[14rem] text-ellipsis` is the containment (see text-shell.ts). `w-fit shrink-0
		// whitespace-nowrap overflow-hidden` alone described a badge that grows to whatever string it
		// is given, refuses to shrink, and -- because `overflow-hidden` had no `text-ellipsis` beside
		// it -- hard-clips mid-glyph the moment anything DOES bound it. Fine for the short labels a
		// badge was designed for, wrong for what callers actually put in one: a model name, a service
		// type, a role. `w-fit` stays, so a short badge is untouched and only an over-long one meets
		// the ceiling; `MultiSelect` still overrides `shrink-0` with `min-w-0 shrink`, because its
		// chips must yield to the field, which is a narrower box than this ceiling. A badge holding a
		// name it might clip should carry `title` -- the ceiling shortens what is shown, it must not
		// lose what it says.
		base: 'h-5 max-w-[14rem] gap-1 rounded-full border border-transparent px-2 py-0.5 text-xs font-medium transition-all has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&>svg]:size-3! focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive group/badge inline-flex w-fit shrink-0 items-center justify-center overflow-hidden text-ellipsis whitespace-nowrap transition-colors focus-visible:ring-[3px] [&>svg]:pointer-events-none',
		variants: {
			variant: {
				default: 'bg-primary text-primary-foreground [a]:hover:bg-primary/80',
				secondary: 'bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80',
				// Tinted destructive: the derived trio, so the ink stays readable on the tint in both
				// schemes (see status-badge/tones.ts).
				destructive:
					'bg-destructive-subtle [a]:hover:bg-destructive-subtle/70 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 text-destructive-strong',
				outline: 'border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground',
				ghost: 'hover:bg-muted hover:text-muted-foreground dark:hover:bg-muted/50',
				link: 'text-primary underline-offset-4 hover:underline'
			}
		},
		defaultVariants: {
			variant: 'default'
		}
	});

	export type BadgeVariant = VariantProps<typeof badgeVariants>['variant'];
</script>

<script lang="ts">
	import type { HTMLAnchorAttributes } from 'svelte/elements';
	import { cn, type WithElementRef } from '../../../utils.js';

	let {
		ref = $bindable(null),
		href,
		class: className,
		variant = 'default',
		children,
		...restProps
	}: WithElementRef<HTMLAnchorAttributes> & {
		variant?: BadgeVariant;
	} = $props();
</script>

<svelte:element
	this={href ? 'a' : 'span'}
	bind:this={ref}
	data-slot="badge"
	{href}
	class={cn(badgeVariants({ variant }), className)}
	{...restProps}
>
	{@render children?.()}
</svelte:element>
