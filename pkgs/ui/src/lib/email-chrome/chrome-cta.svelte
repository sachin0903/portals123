<script lang="ts">
	// THE CTA BLOCK from the kit's layout primitives — deliberately NOT its `Button`.
	//
	// `Button` renders an `<a>` and puts the padding, background and radius on it. Word honours none
	// of those on an inline anchor, so the button loses its shape and becomes coloured text; and it
	// emits no class, so `document.py::MSO_CTA_INK` — the one place Word honours `!important` on a
	// link colour — has nothing to hook. `Row` + `Column` give the `<table>`/`<td>` the Python has
	// always used, and both spread `...restProps`, so the fill style, the class and the editor names
	// all land where they must.
	//
	// What is lost against `Button`: its two MSO `<i>` letter-spacing spacers. Outlook Desktop stopped
	// honouring that hack in 2022 and the Python does not use it either, so this is parity, not a
	// regression — see `kit-parity.test.ts`, which pins that the kit still ships it.
	//
	// The PASTE-THE-LINK sibling is the caller's: `cta_block` returns two siblings, which is why
	// `defaults.py::_guard_all` marks every top-level element rather than one.
	import { Row, Column } from '@better-svelte-email/components';

	let {
		label,
		url,
		cellStyle,
		linkStyle,
		labelStyle,
		ctaClass,
		names = false
	}: {
		/** Sentinels the generator rewrites into Python placeholders. */
		label: string;
		url: string;
		cellStyle: string;
		linkStyle: string;
		labelStyle: string;
		ctaClass: string;
		names?: boolean;
	} = $props();

	const n = (name: string): string | undefined => (names ? name : undefined);
</script>

<!-- `width`/`align` OVERRIDDEN TO `undefined`, which is what removes them: `Row` hardcodes
     `align="center" width="100%"` ahead of its own `...restProps`, and a button that spans its
     container is not a button. With the width on, this rendered as a full-bleed bar across the card
     with the label pinned 24px from the left edge — a pill takes its size from the cell's padding
     around the label, which means the table must carry NO width at all.
     Nothing in the suite caught it: every assertion about the button is about its style layers, and
     they all still passed. `test_the_cta_table_shrinks_to_its_label` is the one that would now. -->
<Row
	data-gjs-name={n('Button')}
	width={undefined}
	align={undefined}
	border={0}
	cellpadding="0"
	cellspacing="0"
	style="border-collapse:collapse;margin:28px 0 0"
>
	<Column data-gjs-name={n('Button fill')} style={cellStyle}>
		<a data-gjs-name={n('Button link')} class={ctaClass} href={url} style={linkStyle}>
			<span data-gjs-name={n('Button label')} style={labelStyle}>{label}</span>
		</a>
	</Column>
</Row>
