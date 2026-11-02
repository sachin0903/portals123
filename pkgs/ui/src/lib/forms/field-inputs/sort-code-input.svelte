<script lang="ts">
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import { Input } from '../../components/ui/input/index.js';
	import { attachDigitMask, formatSortCode, canonicalSortCode } from '../editor/masks';

	let {
		value = '',
		onchange,
		placeholder = '',
		invalid = false,
		verified = false,
		id,
		name
	}: {
		value?: unknown;
		onchange: (v: string) => void;
		placeholder?: string;
		invalid?: boolean;
		/** A bank details check confirmed this sort code. The bank's logo sits on the details card. */
		verified?: boolean;
		id?: string;
		name?: string;
	} = $props();

	let field = $state<HTMLInputElement | null>(null);
	const seed = () => formatSortCode(String(value ?? ''));
	let text = $state(seed());
	// The last prop applied to the box. Typing updates the box before the parent echoes the
	// canonical value back; applying that stale prop is what showed an empty (or zero) sort
	// code until the step was opened again and the saved answer remounted the field.
	let seen = $state<string | undefined>(undefined);

	$effect(() => {
		const incoming = String(value ?? '');
		if (incoming === seen) return;
		const first = seen === undefined;
		seen = incoming;
		if (first) return;
		const incomingDigits = incoming.replace(/\D/g, '');
		if (incomingDigits === text.replace(/\D/g, '')) return;
		text = formatSortCode(incoming);
	});

	$effect(() => {
		const el = field;
		if (!el) return;
		return attachDigitMask(el, {
			canonical: canonicalSortCode,
			format: formatSortCode,
			onchange: (next) => {
				text = formatSortCode(next);
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
		maxlength={12}
		{placeholder}
		autocomplete="off"
		class={verified ? 'pr-10' : undefined}
		aria-invalid={invalid || undefined}
	/>
	{#if verified}
		<span class="pointer-events-none absolute inset-y-0 right-2.5 flex items-center">
			<CircleCheckIcon class="text-success size-5" />
			<span class="sr-only">Bank details confirmed</span>
		</span>
	{/if}
</div>
