<script lang="ts">
	// The area a right-click opens the menu over.
	//
	// It anchors the content at the POINTER, not at itself: `ContextMenuTriggerState` records
	// `event.clientX/clientY` and hands the floating layer a zero-size virtual element there. Two things
	// follow, and both matter to the one caller in this repo (the email editor's canvas, whose real
	// right-click happens inside an iframe this element cannot see):
	//
	//  • a SYNTHETIC `contextmenu` MouseEvent dispatched on this element opens the menu correctly, as
	//    long as it carries translated `clientX`/`clientY` and has not had `preventDefault()` called on
	//    it — the state machine returns early on `defaultPrevented`;
	//  • the anchor has no width, so the content must NOT size itself from
	//    `--bits-context-menu-anchor-width` the way a dropdown's does. See the content wrapper.
	import { ContextMenu as ContextMenuPrimitive } from 'bits-ui';

	let { ref = $bindable(null), ...restProps }: ContextMenuPrimitive.TriggerProps = $props();
</script>

<ContextMenuPrimitive.Trigger bind:ref data-slot="context-menu-trigger" {...restProps} />
