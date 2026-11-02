<script lang="ts" module>
	/**
	 * "Gio Matonti" → "Gio M.": the first name and the last initial, which is how a colleague is
	 * referred to across a desk and all a book listing needs to say — the face beside it carries
	 * the rest, and the tooltip the whole name. A full name in a 12% column was truncated to
	 * "Gio ..." on almost every row, which said less than the short form does. One word (an email,
	 * a mononym) is left as it is: there is no surname to shorten.
	 */
	export function shortName(name: string): string {
		const parts = name.trim().split(/\s+/).filter(Boolean);
		const first = parts[0];
		const last = parts[parts.length - 1];
		if (!first) return '';
		if (parts.length === 1 || !last) return first;
		return `${first} ${last[0]!.toUpperCase()}.`;
	}
</script>

<script lang="ts">
	/**
	 * Advisers as faces then names — and when a client has more than one, the faces stack.
	 *
	 * The adviser column was the widest thing in the book listings, because `advisers.join(', ')` has
	 * no upper bound: three names set the table's width for every row. So the names clip to their
	 * column and the whole list is on hover (`TruncatedText`), while the avatars carry the "how many"
	 * at a glance without spending width on it — overlapping, so four people cost barely more room
	 * than two.
	 *
	 * `PersonAvatar` does the colour, deliberately: it derives a deterministic hue from the name and
	 * roots it in the firm's brand, so one adviser is one colour on every surface in the product. A
	 * second implementation here would be a second palette.
	 *
	 * The avatars are `aria-hidden` (PersonAvatar sets it), so the names beside them are the
	 * accessible content — which is why the text is never replaced by the faces, only summarised.
	 */
	import PersonAvatar from './person-avatar.svelte';
	import { TruncatedText } from '../ui/truncated-text/index.js';
	import { cn } from '../../utils.js';

	let {
		names,
		max = 3,
		size = 'xs',
		placeholder = '—',
		class: className
	}: {
		names: (string | null | undefined)[];
		/** Faces shown before the rest become a count. Three fits the book's adviser column; a wider
		 *  surface can raise it. */
		max?: number;
		/** `xs` by default — the badge height, so the face sits level with the Accounts pills and the
		 *  row keeps its height. A record's team panel, with room, passes `sm`. */
		size?: 'xs' | 'sm' | 'md';
		/** Shown when nobody is assigned — a dash, not an empty cell, so the row still reads. */
		placeholder?: string;
		class?: string;
	} = $props();

	const people = $derived(names.filter((n): n is string => !!n && n.trim() !== ''));
	const shown = $derived(people.slice(0, max));
	const extra = $derived(people.length - shown.length);
	/** The full list — the tooltip's text, and what a reader gets on hover. */
	const label = $derived(people.join(', '));
	/**
	 * What is DISPLAYED: the first adviser's SHORT name ("Craig C."), then a count. Joining every
	 * name set the column's width by how many advisers the busiest client has — "Craig Currie,
	 * Sachin Sharma" is twice the string of "Craig Currie +1" for a fact the second states just as
	 * well, and in a book listing that difference was enough to push the table into a horizontal
	 * scrollbar. Even the one full name then truncated to "Craig ..." on most rows of a narrow
	 * table, which says less than "Craig C." does in the same room.
	 *
	 * The names are not lost: the faces beside this are one per adviser, and the tooltip carries the
	 * list in full (always, now that the displayed form is never the whole name). This is a summary
	 * of a summary, which is the right trade in a cell that has to share a row with three money
	 * columns.
	 */
	const display = $derived(
		people.length <= 1
			? shortName(people[0] ?? '')
			: `${shortName(people[0]!)} +${people.length - 1}`
	);
	/** The tooltip has something to add whenever the display is not the full list verbatim. */
	const summarised = $derived(display !== label);
</script>

{#if people.length === 0}
	<span class={cn('text-muted-foreground', className)}>{placeholder}</span>
{:else}
	<span class={cn('flex min-w-0 items-center', size === 'xs' ? 'gap-1.5' : 'gap-2', className)}>
		<!-- `shrink-0` on the face strip and `min-w-0` on the names: the faces are a fixed cost and the
		     names are what gives way. Without both, the strip would be squashed instead of the text —
		     the failure text-shell.ts calls shape 2. -->
		<span class="flex shrink-0 items-center">
			{#each shown as name (name)}
				<!-- `ring-background` rather than the avatar's own hairline: overlapping discs need a cut
				     in the colour BEHIND them to read as separate, and `-ml-1.5` is the overlap. `first:`
				     resets it so the strip starts flush with the column. -->
				<PersonAvatar {name} {size} class="ring-background -ml-1.5 ring-2 first:ml-0" />
			{/each}
			{#if extra > 0}
				<!-- The overflow count wears the same disc so the strip stays one shape. It is not an
				     avatar: it names no one, so it takes the muted surface rather than a person's hue. -->
				<span
					class={cn(
						'bg-muted text-muted-foreground ring-background -ml-1.5 inline-flex shrink-0 items-center justify-center rounded-full font-semibold ring-2',
						{ xs: 'size-5 text-[0.6rem]', sm: 'size-7 text-[0.7rem]', md: 'size-9 text-xs' }[size]
					)}
					aria-hidden="true">+{extra}</span
				>
			{/if}
		</span>
		<!-- `full` is the whole list; the cell shows the summary. The tooltip gates itself on real
		     overflow, so a name that IS the display (a mononym, an email) shows nothing extra — but a
		     summarised one always has more to say than it displays, which is exactly what `full` is
		     for. -->
		<TruncatedText
			text={display}
			full={summarised ? label : undefined}
			class="text-muted-foreground"
		/>
		{#if summarised}
			<!-- The names the summary leaves out, for a screen reader. The avatars are `aria-hidden` and
			     the tooltip's content does not exist in the DOM until it opens, so without this the only
			     thing announced for a client with three advisers was "Zoe L. +2" — the summary had
			     quietly become the whole answer for anyone not using a mouse. -->
			<span class="sr-only">{label}</span>
		{/if}
	</span>
{/if}
