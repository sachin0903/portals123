<script lang="ts">
	// `preventDefault` first: bits-ui runs our `onclick`, then its own toggle (which inverts the
	// box) unless prevented. `onclick`, not `onCheckedChange`, for `shiftKey`.
	import { Checkbox } from '../../ui/checkbox/index.js';
	import type { SelectionState } from './selection.js';

	let {
		state,
		label,
		disabled = false,
		ontoggle
	}: {
		state: SelectionState | boolean;
		label: string;
		disabled?: boolean;
		ontoggle: (e: MouseEvent) => void;
	} = $props();
</script>

<Checkbox
	checked={state === true || state === 'all'}
	indeterminate={state === 'some'}
	{disabled}
	onclick={(e) => {
		e.preventDefault();
		ontoggle(e);
	}}
	aria-label={label}
/>
