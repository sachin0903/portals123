<script lang="ts" module>
	/**
	 * A PERSON's avatar: deterministic initials in a deterministic colour, so one colleague reads
	 * the same everywhere.
	 *
	 * FOR STAFF, NOT FOR CLIENTS. A client wears `ClientAvatar` — one muted disc, a person or a
	 * building, no colour — because down a column of the book thirty hues beside the money mean
	 * nothing. The people AROUND a client are the opposite case: an adviser stack is a handful of
	 * faces you are meant to tell apart at a glance, and a row of identical grey discs is a blob.
	 * Two looks on purpose, one per question — "which client is this row" against "who are these
	 * people".
	 *
	 * The tone is `personTone` (person-tone.ts): a step of the firm's brand hue, not a hue of its
	 * own.
	 */
	import { personTone as tone } from './person-tone.js';

	function initials(name: string): string {
		const parts = name.trim().split(/\s+/).filter(Boolean);
		const first = parts[0];
		const last = parts[parts.length - 1];
		if (!first || !last) return '–';
		if (parts.length === 1) return first.slice(0, 2).toUpperCase();
		return ((first[0] ?? '') + (last[0] ?? '')).toUpperCase();
	}
</script>

<script lang="ts">
	import { cn } from '../../utils.js';

	let {
		name,
		size = 'md',
		initials: initialsOverride,
		class: className
	}: {
		name: string;
		/** `xs` is the table size — the height of a badge, so a face sits level with the pills in
		 *  the next column and costs the row no height. */
		size?: 'xs' | 'sm' | 'md' | 'lg';
		/** Use these initials instead of deriving them from `name`. For a name that is not a person's
		 *  name: the Events stream identifies an actor by whatever it holds — often an email — and its
		 *  own `initialsOf` splits on `.`/`_`/`@` too, so `m.cole@example.com` reads as "MC" rather than
		 *  the "M." a whitespace split gives. The TONE still comes from `name`, which is the point of
		 *  sharing this component: one person is one tone everywhere in the product. */
		initials?: string;
		class?: string;
	} = $props();

	const px = {
		xs: 'size-5 text-[0.6rem]',
		sm: 'size-7 text-[0.7rem]',
		md: 'size-9 text-xs',
		lg: 'size-12 text-sm'
	};
</script>

<span
	class={cn(
		// The ink comes from `tone()` with the fill, because it depends on the step's lightness. The
		// hairline uses --foreground so it reads in both schemes.
		'ring-foreground/10 inline-flex shrink-0 items-center justify-center rounded-full font-semibold ring-1',
		px[size],
		className
	)}
	style={tone(name)}
	aria-hidden="true"
>
	{initialsOverride ?? initials(name)}
</span>
