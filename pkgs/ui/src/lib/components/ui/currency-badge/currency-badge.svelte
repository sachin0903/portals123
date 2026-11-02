<script lang="ts">
	/**
	 * An ISO 4217 code as a pill, with the country's flag as a small disc on the left: `🇬🇧 GBP`,
	 * `🇺🇸 USD`, `🇪🇺 EUR`. The one way a currency column prints its code — six listings carried
	 * the same `<Badge variant="outline">{currency}</Badge>` before this existed.
	 *
	 * The code is always printed; the flag is an accent on a value that is legible without it,
	 * so it is `alt=""` and hidden from assistive tech rather than a second reading of the same
	 * fact. A code with no country (`XCD`, `XAU` — see currency-flags.ts) keeps a neutral disc in
	 * the flag's place, so the codes in a column start at the same x whether or not each has one.
	 *
	 * `data-icon="inline-start"` is the Badge's own hook: it tightens the left padding so the disc
	 * sits where a leading icon would. The hairline ring is what keeps a white-edged flag (Japan,
	 * Switzerland's corners) reading as a disc on the outline badge's own background.
	 */
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '../../../utils.js';
	import { Badge } from '../badge/index.js';
	import CurrencyFlag from './currency-flag.svelte';

	let {
		code,
		class: className,
		...restProps
	}: Omit<HTMLAttributes<HTMLSpanElement>, 'children'> & {
		/** The ISO 4217 code. Normalised to upper case for display and for the flag lookup. */
		code: string;
	} = $props();

	const iso = $derived(code.trim().toUpperCase());
</script>

<Badge variant="outline" class={cn('font-normal', className)} {...restProps}>
	<CurrencyFlag code={iso} data-icon="inline-start" />
	{iso}
</Badge>
