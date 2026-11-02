<script lang="ts">
	// A SQL result set as a grid — the console's answer and a model's row preview.
	//
	// An ADAPTER over `@graphene/ui`'s DataTable, not a table of its own. It used to be a raw
	// `<table>` with its own header fill, row rules, hover, padding and truncation, which is the
	// hand-rolled copy docs/design-language.md rules out: it was the only table in the admin portal
	// that did not match the others, and it drifted a shade of every one of those properties.
	//
	// What the adapter is actually for is the SHAPE. A query answers with positional rows
	// (`CellValue[][]`) over columns it only learns at runtime, where the shared table takes
	// objects and a typed column list — so the rows are keyed by ordinal here and nowhere else.
	// Ordinal, not column name: SQL happily returns the same name twice (`SELECT a.id, b.id`), and
	// keying by name would silently drop the second one.
	import { DataTable, type DataTableColumn } from '@graphene/ui';
	import type { CellValue } from '../rows';

	let { columns, rows }: { columns: string[]; rows: CellValue[][] } = $props();

	type Row = { i: number; cells: CellValue[] };

	const tableColumns = $derived<DataTableColumn<Row>[]>(
		columns.map((name, i) => ({
			key: String(i),
			header: name,
			// Figures right-align on the shared decimal point, the rule every other table follows.
			// Decided per COLUMN off its first row rather than per cell: aligning individual cells by
			// their own type makes a column with one NULL jump between edges down its length.
			align: typeof rows[0]?.[i] === 'number' ? ('right' as const) : undefined
		}))
	);

	const tableRows = $derived<Row[]>(rows.map((cells, i) => ({ i, cells })));

	// `sortDisabled`: a console result is whatever the query's own ORDER BY produced, over a LIMIT.
	// Sorting the returned page here would reorder those rows and present it as the order of the
	// result — the same lie the API log and the entity tables refuse.
</script>

<DataTable
	columns={tableColumns}
	rows={tableRows}
	getKey={(row) => row.i}
	label="Query results"
	sortDisabled
	empty="The query returned no rows."
	class="rounded-xl border font-mono text-xs"
>
	{#snippet cell(row, column)}
		{@const value = row.cells[Number(column.key)]}
		<span
			class="block max-w-80 truncate"
			title={typeof value === 'string' && value.length > 40 ? value : undefined}
		>
			{typeof value === 'boolean' ? String(value) : value}
		</span>
	{/snippet}
</DataTable>
