<script lang="ts">
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import { Input } from '../../components/ui/input/index.js';
	import { attachDigitMask, formatBankAccountNumber } from '../editor/masks';

	let {
		value = '',
		onchange,
		placeholder = '12345678',
		invalid = false,
		verified = false,
		mismatch = false,
		id,
		name
	}: {
		value?: unknown;
		onchange: (v: string) => void;
		placeholder?: string;
		invalid?: boolean;
		/** A bank details check confirmed this number belongs with the sort code. */
		verified?: boolean;
		/** A bank details check found no account with this number at the sort code. */
		mismatch?: boolean;
		id?: string;
		name?: string;
	} = $props();

	let field = $state<HTMLInputElement | null>(null);
	const seed = () => formatBankAccountNumber(String(value ?? ''));
	let text = $state(seed());

	$effect(() => {
		text = formatBankAccountNumber(String(value ?? ''));
	});

	$effect(() => {
		const el = field;
		if (!el) return;
		return attachDigitMask(el, {
			canonical: formatBankAccountNumber,
			format: formatBankAccountNumber,
			onchange: (next) => {
				text = next;
				onchange(next);
			}
		});
	});
</script>

<div class="relative">
	<Input
		bind:ref={field}
		bind:value={text}
		{id}
		{name}
		inputmode="numeric"
		maxlength={8}
		{placeholder}
		autocomplete="off"
		class={verified ? 'pr-10' : undefined}
		aria-invalid={invalid || mismatch || undefined}
	/>
	{#if verified}
		<span class="pointer-events-none absolute inset-y-0 right-2.5 flex items-center">
			<CircleCheckIcon class="text-success size-5" />
			<span class="sr-only">Bank details confirmed</span>
		</span>
	{/if}
</div>
