<script lang="ts">
	/**
	 * The wrappers a client holds, as a row of tags: "GIA · ISA · SIPP". What a book is actually
	 * scanned for ("which of my clients has a SIPP?"), where the bare count the column used to
	 * print cannot answer. Shared by the /clients listing and the dashboard's book, which are the
	 * same table and must not read differently.
	 *
	 * `AdviserStack`'s idiom, and for the same reason: a cell in a table of eight columns cannot be
	 * as wide as the busiest client's list, so the first few are shown and the rest become a count.
	 * The full list is announced (`sr-only`), because a summary must not quietly become the whole
	 * answer for anyone not using a mouse. The tags WRAP: the wrapper vocabulary is open
	 * ("TPPENSION" is a real code), so no column width can promise to hold three on one line, and
	 * a second line of tags sits inside a row that is already two lines tall — the name and the
	 * date of birth under it — where a tag running under the next column's pill did not.
	 */
	import { Badge } from '../../ui/badge/index.js';
	import { cn } from '../../../utils.js';

	let {
		wrappers,
		count = wrappers.length,
		max = 3,
		placeholder = '—',
		class: className
	}: {
		/** The distinct wrappers, sorted by the API (`account_types`). */
		wrappers: readonly string[];
		/** Accounts held, for the announced total — more than the tags when two share a wrapper. */
		count?: number;
		/** Tags shown before the rest become a count. Three fits the shelf as it stands (GIA · ISA
		 *  · JISA · SIPP) with one to spare, and the product vocabulary is open. */
		max?: number;
		/** Shown when no account is held — a dash, not an empty cell, so the row still reads. */
		placeholder?: string;
		class?: string;
	} = $props();

	const shown = $derived(wrappers.slice(0, max));
	const extra = $derived(wrappers.length - shown.length);
</script>

{#if wrappers.length === 0}
	<span class={cn('text-muted-foreground', className)}>{placeholder}</span>
{:else}
	<span class={cn('flex min-w-0 flex-wrap items-center gap-1', className)}>
		{#each shown as wrapper (wrapper)}
			<Badge variant="outline" class="shrink-0 font-normal">{wrapper}</Badge>
		{/each}
		{#if extra > 0}
			<span class="text-muted-foreground shrink-0 text-xs" aria-hidden="true">+{extra}</span>
		{/if}
		<!-- ONE expression, not three interpolations on separate lines: the markup's own newlines
		     and indentation survive into the DOM as whitespace, so a reader was announced
		     "GIA, ISA · 6      accounts". -->
		<span class="sr-only"
			>{`${wrappers.join(', ')} · ${count} ${count === 1 ? 'account' : 'accounts'}`}</span
		>
	</span>
{/if}
