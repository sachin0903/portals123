<script lang="ts">
	import { Collapsible as CollapsiblePrimitive } from 'bits-ui';
	import { cn } from '../../../utils.js';

	let {
		ref = $bindable(null),
		class: className,
		...restProps
	}: CollapsiblePrimitive.ContentProps = $props();
</script>

<!--
  Visibility is driven by `data-state` and `display: none`, NOT by the primitive's presence machine.

  Two defects this avoids, both observed:

  1. **A panel that says it is open and isn't.** The primitive's `hidden` attribute tracks a presence
     state machine seeded at construction, and an open state that arrives at HYDRATION rather than
     from a later click never transitions it: the trigger reported `aria-expanded="true"` while the
     panel stayed `hidden`. Any consumer restoring a persisted open state (the sidebar restores the
     user's collapsed groups) hits it. `data-state` comes straight off the root's `open`, so it cannot
     disagree with the trigger.

  2. **A keyboard trap.** The prettier alternative — `forceMount` with an animated
     `h-0 overflow-hidden` panel — leaves the links inside a closed section focusable: you tab off
     the last visible row into rows nobody can see. `display: none` takes them out of the tab order
     the same way the primitive's `hidden` would. The chevron carries the motion instead.

  The cost of `forceMount` is that closed content stays in the DOM. For nav sections that is a few
  anchors; a consumer mounting something expensive should pass `forceMount={false}` and accept the
  presence machine's behaviour.
-->
<CollapsiblePrimitive.Content
	bind:ref
	data-slot="collapsible-content"
	forceMount
	class={cn('data-[state=closed]:hidden', className)}
	{...restProps}
/>
