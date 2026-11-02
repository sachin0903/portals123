<script lang="ts">
	// The rail's idle state: the page is unselectable (`create.ts::protectPage`), so its background/inset
	// were unreachable via the rail, which spent 264px saying "Select a component". Real serialised style throughout (`PAGE_INSET`/`PAGE_MIN_HEIGHT_PX`) — what this panel writes is what the recipient's inbox gets.
	import PropertyRow from '../ui/property-row/property-row.svelte';
	import ColorInput from '../ui/color-input/color-input.svelte';
	import BoxSides from '../ui/box-sides/box-sides.svelte';
	import { splitSides, joinSides } from '../ui/box-sides/box-sides.js';

	let {
		background,
		padding,
		brandColours = [],
		disabled = false,
		onchange,
		oninput
	}: {
		/** Each is `''` when unset. brandColours: same swatch list the style rail uses. */
		background: string;
		padding: string;
		brandColours?: readonly string[];
		disabled?: boolean;
		onchange: (patch: { background?: string; padding?: string }) => void;
		/** The same patch MID-GESTURE. Absent ⇒ `onchange` takes both, which is today's behaviour. */
		oninput?: (patch: { background?: string; padding?: string }) => void;
	} = $props();

	// Derived, not state: the caller re-reads the model after every write, so a local copy would be stale.
	const sides = $derived(splitSides(padding));
</script>

<!-- No section band (was a collapsible `InspectorSector` over the panel's only content). TWO
     PROPERTIES, deliberately: the page is the sheet, and its colour and inset are the only things about
     it an author sets. Page text-align and corner radius were dropped — a block declares its own
     alignment and the radius is cosmetic. NO page font or text colour either: Outlook's Word engine
     inherits neither onto a child, so an author would set a typeface, see it in the canvas, and the
     recipient would read the client's default serif — see `setPageStyle`. -->
<div class="flex flex-col gap-1 px-2 pt-2 pb-2">
	<PropertyRow label="Background" for="et-page-bg" set={!!background}>
		<ColorInput
			id="et-page-bg"
			{disabled}
			value={background}
			swatches={brandColours}
			oninput={(value) => (oninput ?? onchange)({ background: value })}
			onchange={(value) => onchange({ background: value })}
		/>
	</PropertyRow>

	<!-- No wrapping row: `BoxSides` draws its own, because linked and split want different ones. -->
	<BoxSides
		idPrefix="et-page-pad"
		label="Padding"
		{disabled}
		set={!!padding}
		values={sides}
		units={['px']}
		min={0}
		onchange={(next) => onchange({ padding: joinSides(next) })}
	/>
</div>
