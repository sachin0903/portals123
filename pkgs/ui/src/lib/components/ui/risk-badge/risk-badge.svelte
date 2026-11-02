<script lang="ts">
	/**
	 * A risk rating as a badge on the green→amber→red ramp: 1 is the success token, the midpoint the
	 * warning token, the top the destructive token (see ./risk.ts for why it is interpolated rather
	 * than three discrete tones, and why hue interpolation is correct here specifically).
	 *
	 * Shaped like StatusBadge on purpose — same pill, same trio of tinted surface, readable ink and
	 * hairline — because it sits in the same tables. The difference is that the trio cannot be
	 * Tailwind classes: the base colour is computed per instance, so it arrives as `--risk` on the
	 * element and the three rungs are derived from it in CSS with the SAME `oklch(from …)` relative
	 * syntax and the SAME `--tone-*` knobs theme.css uses for the static tones. That is what holds
	 * the ramp's hue exactly (a color-mix against the background would drag it toward blue — the
	 * failure theme.css documents) and what makes it flip correctly under `.dark` with no dark-mode
	 * branch here.
	 *
	 * THE NUMBER IS ALWAYS PRESENT. The colour is an accent on a value that is legible without it —
	 * colour alone never carries the meaning, and the title spells out the scale for anyone who
	 * cannot compare two tints.
	 */
	import { cn } from '../../../utils.js';
	import { StatusBadge } from '../status-badge/index.js';
	import { readRisk } from './risk.js';

	let {
		rating,
		of = 10,
		class: className
	}: {
		rating: string | number | null | undefined;
		/** Top of the scale. 10 is Seccl's, but a firm rating 1–7 is not a broken 1–10. */
		of?: number;
		class?: string;
	} = $props();

	const risk = $derived(readRisk(rating, of));
</script>

{#if risk.kind === 'unrated'}
	<!-- Not on the scale: an unset rating, or free text a firm typed. Rendered as what it is rather
	     than coerced onto a step it does not sit on. -->
	<StatusBadge tone="neutral" class={className}>{risk.label}</StatusBadge>
{:else}
	<span
		data-slot="risk-badge"
		class={cn(
			'risk-badge inline-flex w-fit items-center justify-center rounded-full border px-2 py-0.5 text-xs font-medium tabular-nums',
			className
		)}
		style="--risk: {risk.mix}"
		title="Risk {risk.step} of {risk.of}"
	>
		{risk.step}
	</span>
{/if}

<style>
	/* The same three rungs the static tones derive (theme.css → "HUE IS HELD EXACTLY"), off the
	   per-instance base colour. `oklch(from …)` passes `h` through untouched, so a rung can only
	   differ from the ramp colour in lightness and chroma — and the knobs it reads already invert
	   under .dark, which is why there is no dark-mode rule here. */
	.risk-badge {
		background: oklch(from var(--risk) var(--tone-subtle-l) calc(c * var(--tone-subtle-c)) h);
		color: oklch(from var(--risk) var(--tone-strong-l) calc(c * var(--tone-strong-c)) h);
		border-color: oklch(from var(--risk) var(--tone-line-l) calc(c * var(--tone-line-c)) h);
	}
</style>
