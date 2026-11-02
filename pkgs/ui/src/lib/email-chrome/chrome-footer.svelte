<script lang="ts">
	// THE FOOTER as its own table, from the kit's layout primitives.
	//
	// It used to be a bare `<tr>` spliced into the card's open `<tbody>` — the shape
	// `shell.py::footer_row` still describes for its callers ("a ROW rather than a string of copy, so
	// a caller with nothing to put in it emits nothing at all"). That splice is impossible here: the
	// kit has no bare-row primitive, so the card is one table PER ROW and a `<tr>` handed to the
	// footer slot lands inside a `<td>`. An HTML parser repairs that by hoisting the row, which is
	// why it renders at all — but shipping markup that depends on error recovery puts the footer's
	// position at the mercy of each client's parser, and Word's is the one least likely to agree.
	//
	// So the footer carries its own table. The hairline (`border-top`) is what still joins it to the
	// content above, and it renders the same whether the row is a sibling row or a sibling table.
	import { Row, Column } from '@better-svelte-email/components';

	let {
		cellStyle,
		inner,
		names = false
	}: {
		/** Sentinels the generator rewrites into Python placeholders. */
		cellStyle: string;
		inner: string;
		names?: boolean;
	} = $props();

	const n = (name: string): string | undefined => (names ? name : undefined);
</script>

<Row
	data-gjs-name={n('Footer')}
	width="100%"
	border={0}
	cellpadding="0"
	cellspacing="0"
	style="width:100%;border-collapse:collapse"
>
	<Column data-gjs-name={n('Footer content')} style={cellStyle}>{inner}</Column>
</Row>
