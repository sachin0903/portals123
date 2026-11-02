<script lang="ts">
	// The dynamic form's `money` field. An ADAPTER over the shared `MoneyInput`, not a second
	// implementation of one: this file used to carry its own symbol lookup, its own grouping mask
	// (`forms/editor/masks.ts::formatCurrency`) and its own left-hand symbol overlay, so the same
	// concept behaved differently depending on whether you reached it through a form definition or
	// through a hand-written drawer.
	//
	// What stays here is the only genuine difference: the form engine stores every answer as a
	// STRING (it round-trips through JSON and back into a text column), while the shared component
	// binds a number. So this converts at that boundary and nowhere else.
	import MoneyInput from '../../components/ui/money-input/money-input.svelte';

	let {
		value = '',
		onchange,
		currencyCode = 'GBP',
		placeholder = '0.00',
		invalid = false,
		id
	}: {
		value?: unknown;
		onchange: (v: string) => void;
		currencyCode?: string;
		placeholder?: string;
		invalid?: boolean;
		id?: string;
	} = $props();

	// A stored answer can be "1234.5", "1,234.50" or "" — an older submission, a seeded value, or a
	// copy-paste. Parse leniently; a value that is not a number at all becomes empty rather than NaN.
	const amount = $derived.by(() => {
		const raw = String(value ?? '').replace(/[^\d.-]/g, '');
		if (raw === '') return null;
		const parsed = Number(raw);
		return Number.isFinite(parsed) ? parsed : null;
	});
</script>

<MoneyInput
	{id}
	value={amount}
	currency={currencyCode}
	{placeholder}
	{invalid}
	onchange={(next) => onchange(next == null ? '' : String(next))}
/>
