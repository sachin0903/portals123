<script lang="ts">
	import { Collapsible as CollapsiblePrimitive } from 'bits-ui';

	let { ref = $bindable(null), ...restProps }: CollapsiblePrimitive.TriggerProps = $props();
</script>

<!--
  The primitive owns the button element, `type="button"`, `aria-expanded`, `aria-controls` and the
  keyboard handling (Space/Enter). No consumer should be hand-rolling a disclosure out of a raw
  `<button>` + a boolean — that is how nav sections end up unreachable by keyboard.
-->
<CollapsiblePrimitive.Trigger bind:ref data-slot="collapsible-trigger" {...restProps} />
