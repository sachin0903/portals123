<script lang="ts">
	// A job-run message, clamped to two lines so a long error never widens the table; clicking a
	// message that is actually cut off shows all of it, and clicking again folds it back. The same
	// behaviour as a client note on the platform portal (client-record.svelte): the chevron and the
	// click only exist when the clamp hides something, measured on the rendered paragraph rather
	// than guessed from a character count, because the column's width decides how much two lines
	// hold.
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { cn } from '@graphene/ui';

	let { text }: { text: string | null } = $props();

	let expanded = $state(false);
	let overflowing = $state(false);
	let el = $state<HTMLElement | null>(null);

	// Measured while collapsed (the only state in which a clamp can be seen to cut anything off), and
	// again when the box is resized, since a narrower window turns a fitting message into a clipped one.
	$effect(() => {
		if (!el || expanded) return;
		const node = el;
		const measure = () => (overflowing = node.scrollHeight > node.clientHeight + 1);
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(node);
		return () => observer.disconnect();
	});
</script>

{#snippet body()}
	<span
		bind:this={el}
		class={cn(
			'text-muted-foreground min-w-0 flex-1 [overflow-wrap:anywhere] whitespace-pre-wrap',
			!expanded && 'line-clamp-2'
		)}>{text}</span
	>
{/snippet}

{#if !text}
	<span class="text-muted-foreground">—</span>
{:else if overflowing || expanded}
	<button
		type="button"
		class="flex w-full min-w-0 items-start gap-2 text-left"
		aria-expanded={expanded}
		onclick={() => (expanded = !expanded)}
	>
		{@render body()}
		<ChevronDownIcon
			class={cn(
				'text-muted-foreground mt-0.5 size-4 shrink-0 transition-transform',
				expanded && 'rotate-180'
			)}
		/>
	</button>
{:else}
	<div class="flex w-full min-w-0 items-start gap-2">{@render body()}</div>
{/if}
