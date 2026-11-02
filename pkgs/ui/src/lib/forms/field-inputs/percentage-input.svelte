<script lang="ts">
	import { Input } from '../../components/ui/input/index.js';
	import { formatPercentage } from '../editor/masks';

	let {
		value = '',
		onchange,
		placeholder = '0',
		invalid = false,
		id
	}: {
		value?: unknown;
		onchange: (v: string) => void;
		placeholder?: string;
		invalid?: boolean;
		id?: string;
	} = $props();

	const display = $derived(formatPercentage(String(value ?? '')));
</script>

<div class="relative">
	<Input
		{id}
		inputmode="decimal"
		{placeholder}
		value={display}
		class="pr-7"
		autocomplete="off"
		aria-invalid={invalid || undefined}
		oninput={(e) => onchange(formatPercentage(e.currentTarget.value))}
	/>
	<span
		class="text-muted-foreground pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-sm"
		aria-hidden="true">%</span
	>
</div>
