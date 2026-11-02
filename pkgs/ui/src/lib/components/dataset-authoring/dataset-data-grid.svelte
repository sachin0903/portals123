<script lang="ts">
	import { Button, Label } from '@graphene/ui';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import XIcon from '@lucide/svelte/icons/x';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { parseCsvFile } from './csv';

	// Editable grid for a dataset's inline/tabular data — type into cells, add/remove rows &
	// columns, or import a CSV. `columns`/`rows` are bindable so the parent owns serialisation
	// (via `toCsvLine`) and form submission. A stable id namespaces the file input when more than
	// one grid could exist on a page.
	let {
		columns = $bindable(),
		rows = $bindable(),
		id = 'dataset-grid'
	}: {
		columns: string[];
		rows: string[][];
		id?: string;
	} = $props();

	const setColumn = (i: number, val: string): void => void (columns[i] = val);
	const setCell = (r: number, c: number, val: string): void => void (rows[r]![c] = val);

	function addColumn(): void {
		columns = [...columns, `Column ${columns.length + 1}`];
		rows = rows.map((r) => [...r, '']);
	}
	function removeColumn(i: number): void {
		if (columns.length <= 1) return;
		columns = columns.filter((_, c) => c !== i);
		rows = rows.map((r) => r.filter((_, c) => c !== i));
	}
	function addRow(): void {
		rows = [...rows, columns.map(() => '')];
	}
	function removeRow(i: number): void {
		const next = rows.filter((_, r) => r !== i);
		rows = next.length ? next : [columns.map(() => '')];
	}

	// CSV import — read the file text and load it into the grid (first line = headers).
	let importError = $state<string | null>(null);
	async function importCsv(e: Event): Promise<void> {
		importError = null;
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		try {
			const parsed = parseCsvFile(await file.text());
			if (!parsed.columns.length) {
				importError = 'That file looks empty — no header row found.';
			} else {
				columns = parsed.columns;
				rows = parsed.rows.length ? parsed.rows : [parsed.columns.map(() => '')];
			}
		} catch {
			importError = 'Could not read that file.';
		}
		input.value = ''; // let the same file be re-imported
	}

	const filledCount = $derived(rows.filter((r) => r.some((c) => c.trim() !== '')).length);
</script>

<div class="space-y-2">
	<div class="flex items-center justify-between gap-2">
		<Label>Data</Label>
		<div>
			<input type="file" {id} accept=".csv,text/csv" class="hidden" onchange={importCsv} />
			<Button
				type="button"
				variant="outline"
				size="sm"
				onclick={() => document.getElementById(id)?.click()}
			>
				<UploadIcon class="mr-1.5 size-3.5" /> Import CSV
			</Button>
		</div>
	</div>
	{#if importError}
		<p class="text-destructive text-xs">{importError}</p>
	{/if}
	<div class="overflow-x-auto rounded-lg border">
		<table class="w-full border-collapse text-sm">
			<thead>
				<tr class="bg-muted/40">
					{#each columns as col, ci (ci)}
						<th class="border-b border-r p-0 last:border-r-0">
							<div class="flex items-center">
								<input
									value={col}
									oninput={(e) => setColumn(ci, e.currentTarget.value)}
									aria-label={`Column ${ci + 1} name`}
									class="focus:bg-background w-full min-w-28 bg-transparent px-2 py-1.5 text-sm font-medium focus:outline-none"
								/>
								{#if columns.length > 1}
									<button
										type="button"
										onclick={() => removeColumn(ci)}
										aria-label={`Remove column ${ci + 1}`}
										class="text-muted-foreground hover:text-destructive shrink-0 px-1.5"
									>
										<XIcon class="size-3.5" />
									</button>
								{/if}
							</div>
						</th>
					{/each}
					<th class="border-b p-0">
						<button
							type="button"
							onclick={addColumn}
							aria-label="Add column"
							class="text-muted-foreground hover:text-foreground hover:bg-muted flex size-full items-center justify-center px-2 py-1.5"
						>
							<PlusIcon class="size-4" />
						</button>
					</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as row, ri (ri)}
					<tr>
						{#each row as cell, ci (ci)}
							<td class="border-b border-r p-0 last:border-r-0">
								<input
									value={cell}
									oninput={(e) => setCell(ri, ci, e.currentTarget.value)}
									aria-label={`Row ${ri + 1}, ${columns[ci] ?? `column ${ci + 1}`}`}
									class="focus:bg-muted/30 w-full min-w-28 bg-transparent px-2 py-1.5 text-sm focus:outline-none"
								/>
							</td>
						{/each}
						<td class="border-b p-0 text-center">
							<button
								type="button"
								onclick={() => removeRow(ri)}
								aria-label={`Remove row ${ri + 1}`}
								class="text-muted-foreground hover:text-destructive flex size-full items-center justify-center px-2 py-1.5"
							>
								<Trash2Icon class="size-3.5" />
							</button>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	<div class="flex items-center justify-between">
		<Button type="button" variant="ghost" size="sm" onclick={addRow}>
			<PlusIcon class="mr-1.5 size-3.5" /> Add row
		</Button>
		<p class="text-muted-foreground text-xs">
			{filledCount} row{filledCount === 1 ? '' : 's'} · {columns.length} column{columns.length === 1
				? ''
				: 's'} · or import a CSV
		</p>
	</div>
</div>
