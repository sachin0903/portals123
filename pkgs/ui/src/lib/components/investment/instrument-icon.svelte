<script lang="ts">
	// The placeholder logo beside a holding — see instrument-icon.ts for why the letters and the
	// tone are derived the way they are. A real logo replaces this component's INSIDE; every
	// caller (holdings table, allocation lists) keeps the same element and the same size.
	import CoinsIcon from '@lucide/svelte/icons/coins';
	import { cn } from '../../utils.js';
	import {
		INSTRUMENT_TONE_CLASSES,
		instrumentInitials,
		instrumentTone,
		isCashHolding
	} from './instrument-icon.js';

	let {
		name,
		ticker = null,
		/** `sm` for a table row, `md` where the holding is the subject of the line. */
		size = 'sm',
		class: className
	}: {
		name: string;
		ticker?: string | null;
		size?: 'sm' | 'md';
		class?: string;
	} = $props();

	const cash = $derived(isCashHolding(name));
	const tone = $derived(instrumentTone(name, ticker));
	const letters = $derived(instrumentInitials(name));

	const SIZES = {
		sm: 'size-7 text-[0.65rem]',
		md: 'size-9 text-xs'
	} as const;
</script>

<!-- `aria-hidden`: the disc is decoration for a row that already names the instrument in text
     beside it, so announcing "GE" before the name would only add noise for a screen reader. -->
<span
	class={cn(
		'flex shrink-0 items-center justify-center rounded-md font-semibold tracking-tight tabular-nums select-none',
		SIZES[size],
		INSTRUMENT_TONE_CLASSES[tone],
		className
	)}
	aria-hidden="true"
	data-instrument-icon={tone}
>
	{#if cash}
		<CoinsIcon class={size === 'sm' ? 'size-3.5' : 'size-4'} />
	{:else}
		{letters}
	{/if}
</span>
