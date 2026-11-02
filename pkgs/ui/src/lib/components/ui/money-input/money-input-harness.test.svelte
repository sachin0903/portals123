<script lang="ts">
	// Owns the amount the way a filter does, so a test can push a value the field did not type.
	import MoneyInput from './money-input.svelte';

	let shown = $state<number | null>(null);

	export function external(next: number | null): void {
		shown = next;
	}

	export function read(): number | null {
		return shown;
	}
</script>

<MoneyInput value={shown} onchange={(next) => (shown = next)} aria-label="Amount" />
