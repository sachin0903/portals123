<script lang="ts">
	/**
	 * A currency's flag as a small disc — the one `CurrencyBadge` leads with, on its own for a line
	 * too small for a pill (a folded Currency column's code under its amount, a detail rail's value).
	 *
	 * Decorative: the code is always printed beside it, so it is `alt=""` and hidden from assistive
	 * tech. A code with no country (`XCD`, `XAU` — see currency-flags.ts) gets a neutral disc, so a
	 * column of codes still starts at the same x.
	 */
	import type { HTMLAttributes } from 'svelte/elements';
	import { currencyFlag } from '../../../currency-flags.js';
	import { cn } from '../../../utils.js';

	let {
		code,
		class: className,
		...restProps
	}: Omit<HTMLAttributes<HTMLElement>, 'children'> & {
		/** The ISO 4217 code. Normalised to upper case for the flag lookup. */
		code: string;
	} = $props();

	const iso = $derived(code.trim().toUpperCase());
	const flag = $derived(currencyFlag(iso));
</script>

{#if flag}
	<img
		src={flag}
		alt=""
		aria-hidden="true"
		decoding="async"
		draggable="false"
		data-flag={iso}
		{...restProps}
		class={cn('ring-foreground/10 size-3 shrink-0 rounded-full ring-1', className)}
	/>
{:else}
	<span
		aria-hidden="true"
		{...restProps}
		class={cn('bg-muted ring-foreground/10 size-3 shrink-0 rounded-full ring-1', className)}
	></span>
{/if}
