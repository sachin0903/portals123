<script lang="ts">
	/**
	 * A secondary identifier under a name — "Transaction: …" — muted and mono so it reads as a
	 * reference rather than a second title.
	 *
	 * `truncate` only ellipsizes a box that has a width. Table cells are `whitespace-nowrap`, and a
	 * flex parent with `items-start` sizes its children to their text, so a bare nowrap id paints
	 * into the next column. `min-w-0` lets this shrink below that text; `max-w-full` caps it at the
	 * column. The label is `shrink-0`, so a long id gives up characters and "Transaction:" stays
	 * readable.
	 *
	 * The full value is a tooltip, gated the same way `TruncatedText` gates one: measured when the
	 * pointer or focus arrives, and not offered when the line already fits. A native `title` is not
	 * reachable from the keyboard.
	 */
	import * as Tooltip from '../tooltip/index.js';
	import { overflows } from '../truncated-text/overflow.js';
	import { cn } from '../../../utils.js';

	let {
		value,
		label = 'ID',
		class: className
	}: {
		value: string | number | null | undefined;
		label?: string;
		class?: string;
	} = $props();

	let el = $state<HTMLElement | null>(null);
	// Disabled until measurement says otherwise, so a reference that fits does not repeat itself.
	let clipped = $state(false);

	function measure(): void {
		if (el) clipped = overflows(el.scrollWidth, el.clientWidth);
	}

	const text = $derived(value == null ? '' : String(value));
</script>

{#if value != null && text.trim()}
	<Tooltip.Provider delayDuration={300}>
		<Tooltip.Root disabled={!clipped}>
			<span class={cn('flex min-w-0 max-w-full items-baseline text-xs', className)}>
				<span class="shrink-0">{label}: </span>
				<Tooltip.Trigger>
					{#snippet child({ props })}
						<span
							{...props}
							bind:this={el}
							class="text-muted-foreground min-w-0 truncate font-mono"
							onpointerenter={measure}
							onfocusin={measure}>{text}</span
						>
					{/snippet}
				</Tooltip.Trigger>
			</span>
			<Tooltip.Content class="max-w-xs break-words whitespace-normal">{text}</Tooltip.Content>
		</Tooltip.Root>
	</Tooltip.Provider>
{/if}
