<script lang="ts">
	import { cn } from '../../../utils.js';
	import PageJump from './page-jump.svelte';
	import { isGap, itemKey, pageItems } from './page-jump.js';

	let {
		page = $bindable(1),
		pageCount,
		siblingCount = 1,
		href,
		compact = false,
		class: className
	}: {
		/** Current page (1-indexed), bindable. */
		page?: number;
		/** Total number of pages. */
		pageCount: number;
		/** How many page numbers to show either side of the current one (default 1). */
		siblingCount?: number;
		/**
		 * Link mode: page controls render as plain anchors to `href(n)` (e.g. `?page={n}`) instead
		 * of buttons — for SSR-consistent listings whose server loader re-fetches on navigation.
		 * Omit for the default client-side (bindable `page`) behaviour.
		 */
		href?: (page: number) => string;
		/** One notch smaller (size-7, tighter gap) for a thin table footer that wants a trim pager. */
		compact?: boolean;
		class?: string;
	} = $props();

	// The page numbers to render, with a gap standing in for each collapsed run — always including
	// the first and last page (e.g. `1 … 3 [4] 5 … 9`). Small runs are filled with numbers rather
	// than collapsed. The maths lives in `page-jump.ts` so it can be tested without mounting this.
	const items = $derived(pageItems(page, pageCount, siblingCount));

	function go(p: number): void {
		page = Math.min(Math.max(1, p), Math.max(1, pageCount));
	}

	// size-8 keeps the desktop density; pointer-coarse: lifts every control to the 40px touch
	// target on touch devices only, so a paginated table stays tappable on a phone without the
	// desktop row ballooning. `compact` trims one step (size-7) for a thin footer; the touch
	// target still lifts, one notch down with it.
	const control = $derived(
		cn(
			'inline-flex shrink-0 items-center justify-center rounded-md leading-none transition-colors disabled:pointer-events-none disabled:opacity-40',
			compact ? 'size-7 text-xs pointer-coarse:size-9' : 'size-8 text-sm pointer-coarse:size-10'
		)
	);
	const inactive = 'text-muted-foreground hover:bg-accent hover:text-accent-foreground';
	const active = 'bg-accent text-foreground font-medium';
</script>

{#snippet ctrl(text: string, target: number, label: string, disabled: boolean, current: boolean)}
	{#if href}
		{#if disabled}
			<!-- A disabled end stays in the accessibility tree (aria-disabled, labelled) so screen
			     readers hear "First page, disabled" instead of the control vanishing — but with no
			     tabindex it's skipped by the keyboard, matching a disabled button's behaviour. -->
			<span
				role="link"
				aria-disabled="true"
				aria-label={label}
				class={cn(control, inactive, 'pointer-events-none opacity-40')}>{text}</span
			>
		{:else}
			<a
				href={href(target)}
				class={cn(control, current ? active : inactive)}
				aria-label={label}
				aria-current={current ? 'page' : undefined}>{text}</a
			>
		{/if}
	{:else}
		<button
			type="button"
			class={cn(control, current ? active : inactive)}
			onclick={() => go(target)}
			{disabled}
			aria-label={label}
			aria-current={current ? 'page' : undefined}>{text}</button
		>
	{/if}
{/snippet}

{#if pageCount > 1}
	<nav
		class={cn('flex items-center justify-center', compact ? 'gap-0.5' : 'gap-1', className)}
		aria-label="Pagination"
	>
		{#each items as it (itemKey(it))}
			{#if isGap(it)}
				<!-- The gap is a real control now: it opens a "go to page" field naming the run it
				     hides. It used to be a decorative span, which meant a target that looked clickable,
				     was clicked, and did nothing. -->
				<PageJump gap={it} {pageCount} {href} onselect={go} />
			{:else}
				{@render ctrl(String(it), it, `Page ${it}`, false, it === page)}
			{/if}
		{/each}
	</nav>
{/if}
