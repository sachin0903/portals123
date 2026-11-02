<script lang="ts">
	// A GBP figure for dense tables: pounds at full weight, pence smaller and muted, so a
	// column of values reads as a column of pounds without losing the exact amount.
	//
	// `text-[0.85em]` (not a fixed text-xs) keeps the pence proportional to whatever size
	// the surrounding cell sets, so the same component works in a table row and in a
	// page-header stat without looking wrong in either.
	//
	// `[font-family:var(--money-display)]` is the finish-axis numeral-face knob. The token has
	// NO theme.css default on purpose: with it unset the var() substitution fails, the
	// declaration is invalid at computed-value time and font-family behaves as `unset` — i.e.
	// INHERITS, so a mono reference column keeps mono figures and a display-face hero keeps its
	// numerals (guaranteed by css-variables-1 §3.1's IACVT rule; do not "fix" this with a
	// fallback). The ledger finish points it at var(--font-display) so every figure takes the
	// firm's display face; under the card finish it stays unset even when font_display is
	// branded — a body serif on every table cell would surprise, the display face is opt-in
	// per surface there.
	import { GAIN_TONES, gainDirection, minorDigits, splitMoney } from '../../../money.js';
	import { cn } from '../../../utils.js';

	let {
		amount,
		/** ISO 4217 code for the figure. Defaults to GBP — a book that is sterling-only
		 *  should not have to say so on every cell. A USD/EUR row must pass its own. */
		currency = 'GBP',
		/** Always show a +/- sign — for gains/losses rather than balances. */
		signed = false,
		/**
		 * Tint the figure by its sign — for a GROWTH or a movement, where up and down are the fact
		 * being scanned for. Not for a balance, where a negative is an overdraft rather than bad
		 * news, which is why this is opt-in and the default leaves the colour alone.
		 *
		 * TWO tinted tones, because the estate has two token families for "good/bad" and this
		 * component is rendered under both: `directional` paints from the STATUS pair
		 * (success/destructive), which is what the adviser book uses; `money` paints from the
		 * MONEY pair (`--positive`/`--negative`), which is what the client portal's own figures
		 * use. Picking one for everything would give one of the two surfaces a second green, so
		 * the caller says which family it is in. Worth unifying one day; not silently.
		 *
		 * The sign still renders (`signed`), so colour never carries the meaning on its own — the
		 * rule `RiskBadge` follows for the same reason, and what keeps this readable for anyone who
		 * cannot separate the two hues. Zero is deliberately neutral: it is neither, and tinting it
		 * green would say something happened.
		 *
		 * REACH FOR THIS rather than passing a colour in `class`: only a tone reaches the PENCE.
		 * A caller-supplied class tints the pounds and leaves `text-muted-foreground` on the minor
		 * part, so a red total ends in grey pence — which is what this component's own comment
		 * below has always said, and what a caller cannot fix from outside.
		 */
		tone = 'none',
		/** Rendered when there is no figure at all (null/undefined/non-finite). */
		placeholder = '—',
		class: className,
		minorClass
	}: {
		amount: number | null | undefined;
		currency?: string | null;
		signed?: boolean;
		tone?: 'none' | 'directional' | 'money';
		placeholder?: string;
		class?: string;
		minorClass?: string;
	} = $props();

	const parts = $derived(
		amount == null || !Number.isFinite(amount) ? null : splitMoney(amount, { signed, currency })
	);
	// The tones are `GAIN_TONES` (money.ts), shared with every other gain/loss figure. Zero is
	// judged at the currency's minor unit, so a float residue never tints "£0.00".
	const direction = $derived(gainDirection(amount, minorDigits(currency)));
	const toneClass = $derived(
		tone === 'none' || direction === null ? undefined : GAIN_TONES[tone][direction]
	);
</script>

{#if parts === null}
	<span
		class={cn('text-muted-foreground tabular-nums [font-family:var(--money-display)]', className)}
		>{placeholder}</span
	>
{:else}
	<span class={cn('tabular-nums [font-family:var(--money-display)]', toneClass, className)}
		><!-- The pence INHERIT a directional tone: muting them is what makes a figure read as pounds
		     first, but on a tinted gain or loss `text-muted-foreground` overrode the tint and left grey
		     pence hanging off a red number. Under a tone the pence keep the tint and step back with
		     opacity instead, which reads the same and inherits by construction. -->{parts.major}<span
			class={cn('text-[0.85em]', toneClass ? 'opacity-70' : 'text-muted-foreground', minorClass)}
			>{parts.minor}</span
		></span
	>
{/if}
