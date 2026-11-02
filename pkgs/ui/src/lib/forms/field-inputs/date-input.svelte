<script lang="ts">
	import { Input } from '../../components/ui/input/index.js';

	let {
		value = undefined,
		onchange,
		placeholder,
		invalid = false,
		id
	}: {
		value?: unknown;
		onchange: (v: string | undefined) => void;
		placeholder?: string;
		invalid?: boolean;
		id?: string;
	} = $props();

	// A native date input opens the browser's calendar and uses ISO YYYY-MM-DD —
	// the same value we store, so no conversion is needed. Any stored time part is
	// trimmed for the control.
	const isoValue = $derived(typeof value === 'string' ? value.slice(0, 10) : '');

	// Deliberately NO click handler calling `showPicker()` on the whole input. It reads as a
	// convenience and it takes the keyboard away: the calendar opens over the field on every click,
	// so the click lands on the overlay instead of the day/month/year segment it was aimed at and
	// the field can never be focused for typing. A date of birth decades back is then only
	// reachable by paging the calendar month by month. Clicking now focuses the segment under the
	// pointer, and the native calendar indicator inside the field still opens the picker for
	// anyone who wants it — so nothing was lost. Pinned by date-input.dom.test.ts.

	// COMMIT ON `input`, NOT ON `change` ALONE. This was the one field kind in the whole form whose
	// answer lived in the DOM and nowhere else until the control was left: `change` on a native date
	// fires when the value is complete AND on blur, so between typing a date and leaving the field
	// the form did not have it. Everything that re-renders the form mid-entry — picking from a
	// dropdown, a `computedFrom` field resolving, a conditional section appearing — happened while
	// the answer was still only on screen. What the adviser then saw was a date sitting in a filled-
	// in box that Save reported as missing, which reads as the form losing text rather than as never
	// having been told. `change` is KEPT beside it: a browser that commits a picker selection without
	// an `input` event (and assistive tooling that sets the value directly) still has to land.
	function commit(el: HTMLInputElement) {
		onchange(el.value || undefined);
	}
</script>

<Input
	{id}
	type="date"
	{placeholder}
	value={isoValue}
	autocomplete="off"
	aria-invalid={invalid || undefined}
	class="[&::-webkit-calendar-picker-indicator]:cursor-pointer dark:[&::-webkit-calendar-picker-indicator]:invert"
	oninput={(e) => commit(e.currentTarget)}
	onchange={(e) => commit(e.currentTarget)}
/>
