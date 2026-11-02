<script lang="ts">
	import type { FormField, FormScoring } from '@graphene/ui';
	import { Button, Badge, ConfirmDialog, StatusBadge, fieldWidthSpan } from '@graphene/ui';
	import { untrack } from 'svelte';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import FieldEditorSheet from './field-editor-sheet.svelte';
	import ScoringPanel from './scoring-panel.svelte';

	let {
		fields = [],
		onchange,
		scoring = null,
		onscoring
	}: {
		fields?: FormField[];
		onchange?: (fields: FormField[]) => void;
		scoring?: FormScoring | null;
		onscoring?: (scoring: FormScoring | null) => void;
	} = $props();

	type BuilderSection = { id: string; name: string; fields: FormField[] };

	let secCounter = 0;
	let fldCounter = 0;

	function seed(): BuilderSection[] {
		const sorted = fields.slice().sort((a, b) => a.sortOrder - b.sortOrder);
		const order: string[] = [];
		const map = new Map<string, FormField[]>();
		for (const f of sorted) {
			const key = f.section ?? '';
			if (!map.has(key)) {
				map.set(key, []);
				order.push(key);
			}
			map.get(key)!.push(f);
		}
		if (order.length === 0) order.push('');
		return order.map((name) => {
			secCounter += 1;
			return { id: `sec-${secCounter}`, name, fields: map.get(name) ?? [] };
		});
	}

	let sections = $state<BuilderSection[]>(untrack(seed));
	let sheetOpen = $state(false);
	let editing = $state<FormField | null>(null);
	let editingSectionId = $state<string | null>(null);
	let dragFieldUid = $state<string | null>(null);
	let dragSectionId = $state<string | null>(null);
	let dragOverSectionId = $state<string | null>(null);
	let pendingDeleteId = $state<string | null>(null);
	// Live drop indicator while dragging a field: which card is hovered and on which half (an
	// insertion bar on that edge previews the join), or which section's empty space is hovered
	// (a full-row line previews "own row at the end").
	let dropHint = $state<{ uid: string; side: 'left' | 'right' } | null>(null);
	let dropSectionId = $state<string | null>(null);
	function clearDropHints(): void {
		dropHint = null;
		dropSectionId = null;
	}

	// ── Drag-to-distribute field widths ──────────────────────────────────────────
	// Fields on a row share the section's full width (12 columns). Dragging the divider on a
	// field's right edge redistributes columns between it and the next field, so their combined
	// width is always the full row (one grows, the other shrinks). Dragging a full field's divider
	// in pulls the next field up beside it; dragging back out to full un-pairs them.
	let resizingUid = $state<string | null>(null);

	function setWidth(field: FormField, span: number) {
		const v = { ...(field.validation ?? {}) };
		if (span >= 12)
			delete v.width; // 12 = full row (the default) → store nothing
		else v.width = span;
		field.validation = Object.keys(v).length > 0 ? v : null;
	}

	function startResize(e: PointerEvent, fields: FormField[], index: number) {
		const a = fields[index];
		const b = fields[index + 1];
		if (!a || !b) return; // nothing to redistribute with
		const card = (e.currentTarget as HTMLElement).closest('[data-field-card]');
		const grid = card?.closest('[data-field-grid]');
		if (!(card instanceof HTMLElement) || !(grid instanceof HTMLElement)) return;
		e.preventDefault();
		const aLeft = card.getBoundingClientRect().left;
		const colW = grid.getBoundingClientRect().width / 12;
		const total = fieldWidthSpan(a.validation?.width) + fieldWidthSpan(b.validation?.width);
		const pullUp = total > 12; // a & b currently on separate rows → pulling b up beside a
		const budget = pullUp ? 12 : total; // columns shared across the a|b divider
		resizingUid = a.uid;

		const onMove = (ev: PointerEvent) => {
			let newA = Math.round((ev.clientX - aLeft) / colW);
			newA = Math.min(pullUp ? 12 : budget - 1, Math.max(1, newA));
			if (pullUp && newA === 12) {
				setWidth(a, 12); // dragged back to full → un-paired, each takes its own row
				setWidth(b, 12);
			} else {
				setWidth(a, newA);
				setWidth(b, budget - newA);
			}
		};
		const onUp = () => {
			window.removeEventListener('pointermove', onMove);
			window.removeEventListener('pointerup', onUp);
			resizingUid = null;
			commit();
		};
		window.addEventListener('pointermove', onMove);
		window.addEventListener('pointerup', onUp);
	}

	// Flatten the section structure back to the ordered flat field list the rest
	// of the app uses; each field's `section` + `sortOrder` come from its position.
	function commit() {
		let order = 0;
		const flat: FormField[] = [];
		for (const sec of sections) {
			const name = sec.name.trim() || null;
			for (const f of sec.fields) {
				order += 1;
				flat.push({ ...f, section: name, sortOrder: order });
			}
		}
		onchange?.(flat);
	}

	function insertSection(at: number) {
		secCounter += 1;
		const sec = { id: `sec-${secCounter}`, name: '', fields: [] };
		const clamped = Math.max(0, Math.min(at, sections.length));
		sections = [...sections.slice(0, clamped), sec, ...sections.slice(clamped)];
	}

	// Reorder by dragging a section's grip onto another section (drop-before).
	function dropSection(targetId: string) {
		const dragged = dragSectionId;
		dragSectionId = null;
		dragOverSectionId = null;
		if (!dragged || dragged === targetId) return;
		const from = sections.findIndex((s) => s.id === dragged);
		if (from < 0) return;
		const next = sections.slice();
		const [moved] = next.splice(from, 1);
		if (!moved) return;
		const to = next.findIndex((s) => s.id === targetId);
		next.splice(to < 0 ? next.length : to, 0, moved);
		sections = next;
		commit();
	}

	function deleteSection(id: string) {
		const sec = sections.find((s) => s.id === id);
		if (!sec) return;
		if (sec.fields.length > 0 && pendingDeleteId !== id) {
			pendingDeleteId = id;
			return;
		}
		sections = sections.filter((s) => s.id !== id);
		pendingDeleteId = null;
		if (sections.length === 0) insertSection(0);
		commit();
	}

	function blankField(): FormField {
		fldCounter += 1;
		return {
			uid: `fld-new-${fldCounter}`,
			fieldKey: '',
			fieldType: 'text',
			label: '',
			section: null,
			description: null,
			required: false,
			conditions: null,
			validation: null,
			options: null,
			sortOrder: 0
		};
	}

	function openNew(sectionId: string) {
		editingSectionId = sectionId;
		editing = blankField();
		sheetOpen = true;
	}

	function openEdit(field: FormField) {
		editingSectionId = null;
		editing = field;
		sheetOpen = true;
	}

	function handleSave(built: FormField) {
		const exists = sections.some((s) => s.fields.some((f) => f.uid === built.uid));
		if (exists) {
			sections = sections.map((sec) => ({
				...sec,
				fields: sec.fields.map((f) => (f.uid === built.uid ? built : f))
			}));
		} else {
			const targetId = editingSectionId ?? sections[0]?.id;
			sections = sections.map((sec) =>
				sec.id === targetId ? { ...sec, fields: [...sec.fields, built] } : sec
			);
		}
		commit();
	}

	function removeField(uid: string) {
		sections = sections.map((sec) => ({
			...sec,
			fields: sec.fields.filter((f) => f.uid !== uid)
		}));
		commit();
	}

	// Deleting a field asks first (same treatment as the section delete's confirm strip).
	let fieldDeleteOpen = $state(false);
	let pendingFieldDelete = $state<FormField | null>(null);

	function requestRemoveField(field: FormField) {
		pendingFieldDelete = field;
		fieldDeleteOpen = true;
	}

	// ── Row layout helpers ────────────────────────────────────────────────────────
	// Rows are emergent: fields flow on a 12-col grid, so consecutive fields whose spans sum to ≤12
	// share a row. Dropping a field onto another's edge joins that row (equal split); dropping on a
	// section's empty space makes it a full-width row of its own. At most 4 fields per row (min ¼).
	const MAX_PER_ROW = 4;

	const spanOf = (f: FormField): number => fieldWidthSpan(f.validation?.width);

	function setSpan(f: FormField, span: number): void {
		const v = { ...(f.validation ?? {}) };
		if (span >= 12)
			delete v.width; // 12 = full row (default) → store nothing
		else v.width = span;
		f.validation = Object.keys(v).length > 0 ? v : null;
	}

	/** Split 12 columns as evenly as possible across the fields (remainder to the leftmost). */
	function equalSplit(fields: FormField[]): void {
		const n = fields.length;
		if (n === 0) return;
		const base = Math.floor(12 / n);
		let extra = 12 - base * n;
		for (const f of fields) setSpan(f, base + (extra-- > 0 ? 1 : 0));
	}

	/** Group a section's fields into visual rows by packing spans into 12. */
	function packRows(fields: FormField[]): FormField[][] {
		const rows: FormField[][] = [];
		let cur: FormField[] = [];
		let used = 0;
		for (const f of fields) {
			const s = spanOf(f);
			if (cur.length > 0 && used + s > 12) {
				rows.push(cur);
				cur = [];
				used = 0;
			}
			cur.push(f);
			used += s;
		}
		if (cur.length) rows.push(cur);
		return rows;
	}

	/** Remove the dragged field from wherever it is; refill the row it left (equal split). */
	function extractDragged(dragged: string): FormField | undefined {
		let moved: FormField | undefined;
		sections = sections.map((sec) => {
			const field = sec.fields.find((f) => f.uid === dragged);
			if (!field) return sec;
			moved = field;
			const row = packRows(sec.fields).find((r) => r.some((f) => f.uid === dragged));
			const remaining = row?.filter((f) => f.uid !== dragged) ?? [];
			if (remaining.length) equalSplit(remaining); // the row this field vacated refills to full
			return { ...sec, fields: sec.fields.filter((f) => f.uid !== dragged) };
		});
		return moved;
	}

	/** Drop the dragged field beside `targetUid` (left/right), joining its row (equal split). */
	function dropBeside(targetSectionId: string, targetUid: string, side: 'left' | 'right') {
		const dragged = dragFieldUid;
		dragFieldUid = null;
		if (!dragged || dragged === targetUid) return;
		const moved = extractDragged(dragged);
		if (!moved) return;
		sections = sections.map((sec) => {
			if (sec.id !== targetSectionId) return sec;
			const fields = sec.fields.slice();
			const targetRow = packRows(fields).find((r) => r.some((f) => f.uid === targetUid));
			if (!targetRow) {
				setSpan(moved, 12);
				fields.push(moved);
				return { ...sec, fields };
			}
			if (targetRow.length >= MAX_PER_ROW) {
				// Row is full — drop moved as its own full-width row right after it.
				setSpan(moved, 12);
				const lastUid = targetRow[targetRow.length - 1]!.uid;
				fields.splice(fields.findIndex((f) => f.uid === lastUid) + 1, 0, moved);
				return { ...sec, fields };
			}
			// Rebalance the row (target row + moved) to an equal split, then place moved adjacent.
			const tRowIdx = targetRow.findIndex((f) => f.uid === targetUid);
			const newRow = targetRow.slice();
			newRow.splice(side === 'right' ? tRowIdx + 1 : tRowIdx, 0, moved);
			equalSplit(newRow);
			const at = fields.findIndex((f) => f.uid === targetUid) + (side === 'right' ? 1 : 0);
			fields.splice(at, 0, moved);
			return { ...sec, fields };
		});
		commit();
	}

	/** Drop the dragged field into a section as its own full-width row (at the end). */
	function moveField(targetSectionId: string, beforeUid: string | null) {
		const dragged = dragFieldUid;
		dragFieldUid = null;
		if (!dragged || dragged === beforeUid) return;
		const moved = extractDragged(dragged);
		if (!moved) return;
		setSpan(moved, 12); // own row
		sections = sections.map((sec) => {
			if (sec.id !== targetSectionId) return sec;
			const fields = sec.fields.slice();
			const at = beforeUid == null ? -1 : fields.findIndex((f) => f.uid === beforeUid);
			if (at < 0) fields.push(moved);
			else fields.splice(at, 0, moved);
			return { ...sec, fields };
		});
		commit();
	}

	function typeLabel(t: string): string {
		return t.replace(/([A-Z])/g, ' $1').replace(/^./, (c) => c.toUpperCase());
	}
</script>

<div class="space-y-4">
	<div>
		<h2 class="text-lg font-semibold">Fields</h2>
		<p class="text-muted-foreground text-sm">Group fields into sections, then order them.</p>
	</div>

	{#each sections as sec, si (sec.id)}
		<div
			data-section
			class="bg-card overflow-hidden rounded-xl border transition-all"
			class:opacity-40={dragSectionId === sec.id}
			class:ring-2={dragOverSectionId === sec.id && dragSectionId !== sec.id}
			class:ring-ring={dragOverSectionId === sec.id && dragSectionId !== sec.id}
			class:ring-offset-2={dragOverSectionId === sec.id && dragSectionId !== sec.id}
			role="listitem"
			ondragover={(e) => {
				if (dragSectionId && dragSectionId !== sec.id) {
					e.preventDefault();
					dragOverSectionId = sec.id;
				}
			}}
			ondrop={() => {
				if (dragSectionId) dropSection(sec.id);
			}}
		>
			<div class="bg-muted/30 flex items-center gap-2 border-b px-3 py-2">
				<button
					type="button"
					class="text-muted-foreground hover:text-foreground shrink-0 cursor-grab"
					aria-label="Drag to reorder section"
					title="Drag to reorder"
					draggable="true"
					ondragstart={(e) => {
						dragSectionId = sec.id;
						const card = (e.currentTarget as HTMLElement).closest('[data-section]');
						if (card && e.dataTransfer) {
							e.dataTransfer.effectAllowed = 'move';
							e.dataTransfer.setDragImage(card, 16, 16);
						}
					}}
					ondragend={() => {
						dragSectionId = null;
						dragOverSectionId = null;
					}}
				>
					<GripVerticalIcon class="size-4" />
				</button>
				<input
					bind:value={sec.name}
					oninput={commit}
					placeholder="Untitled section"
					aria-label="Section name"
					class="min-w-0 flex-1 bg-transparent text-sm font-semibold outline-none placeholder:font-normal placeholder:italic"
				/>
				<span class="text-muted-foreground shrink-0 text-xs">
					{sec.fields.length} field{sec.fields.length === 1 ? '' : 's'}
				</span>
				<Button
					variant="ghost"
					size="icon"
					class="text-muted-foreground hover:text-destructive size-7 shrink-0"
					aria-label="Delete section"
					onclick={() => deleteSection(sec.id)}
				>
					<Trash2Icon class="size-4" />
				</Button>
			</div>

			{#if pendingDeleteId === sec.id}
				<div
					class="border-destructive/20 bg-destructive/5 text-destructive flex items-center justify-between gap-3 border-b px-3 py-2 text-sm"
				>
					<span>Delete this section and its {sec.fields.length} field(s)?</span>
					<div class="flex items-center gap-2">
						<Button variant="ghost" size="sm" onclick={() => (pendingDeleteId = null)}
							>Cancel</Button
						>
						<Button variant="destructive" size="sm" onclick={() => deleteSection(sec.id)}
							>Delete</Button
						>
					</div>
				</div>
			{/if}

			<!-- Field list on a 12-column grid: each card spans the field's columns, so fields sit
			     side by side exactly as they'll render. Drag the divider on a card's right edge to
			     redistribute columns with the next field (their combined width stays a full row).
			     Drop target appends to this section. -->
			<div
				class="grid grid-cols-1 items-start gap-1.5 p-2 md:grid-cols-12"
				data-field-grid
				role="list"
				ondragover={(e) => {
					if (!dragFieldUid) return;
					e.preventDefault();
					// Only the grid's own empty space previews "append as own row" — hovering a card
					// bubbles here too, but the card sets its edge hint instead.
					if (!(e.target as HTMLElement).closest('[data-field-card]')) {
						dropSectionId = sec.id;
						dropHint = null;
					}
				}}
				ondragleave={(e) => {
					if (dropSectionId !== sec.id) return;
					const rt = e.relatedTarget as Node | null;
					if (!rt || !(e.currentTarget as HTMLElement).contains(rt)) dropSectionId = null;
				}}
				ondrop={() => {
					if (dragFieldUid) moveField(sec.id, null);
					clearDropHints();
				}}
			>
				{#each sec.fields as field, fi (field.uid)}
					{@const span = fieldWidthSpan(field.validation?.width)}
					<div
						class="bg-background relative flex flex-col gap-2 rounded-lg border px-3 py-2"
						style="grid-column: span {span} / span {span}"
						class:opacity-50={dragFieldUid === field.uid}
						class:ring-2={resizingUid === field.uid}
						class:ring-ring={resizingUid === field.uid}
						data-field-card
						role="listitem"
						draggable="true"
						ondragstart={() => (dragFieldUid = field.uid)}
						ondragend={() => {
							dragFieldUid = null;
							clearDropHints();
						}}
						ondragover={(e) => {
							if (!dragFieldUid) return;
							e.preventDefault();
							// Preview the join: an insertion bar on whichever half is hovered.
							const r = e.currentTarget.getBoundingClientRect();
							const side = e.clientX - r.left < r.width / 2 ? 'left' : 'right';
							if (dropHint?.uid !== field.uid || dropHint.side !== side)
								dropHint = { uid: field.uid, side };
							dropSectionId = null;
						}}
						ondragleave={(e) => {
							if (dropHint?.uid !== field.uid) return;
							// dragleave also fires when moving onto the card's children — only clear when
							// the pointer genuinely left the card.
							const rt = e.relatedTarget as Node | null;
							if (!rt || !(e.currentTarget as HTMLElement).contains(rt)) dropHint = null;
						}}
						ondrop={(e) => {
							if (!dragFieldUid) return;
							e.stopPropagation();
							// Drop on the left/right half → join this field's row on that side.
							const r = e.currentTarget.getBoundingClientRect();
							const side = e.clientX - r.left < r.width / 2 ? 'left' : 'right';
							dropBeside(sec.id, field.uid, side);
							clearDropHints();
						}}
					>
						{#if dropHint?.uid === field.uid && dragFieldUid && dragFieldUid !== field.uid}
							<!-- Insertion bar: previews which side of this card the dragged field will join. -->
							<span
								class="bg-primary pointer-events-none absolute inset-y-1 z-20 w-1 rounded-full {dropHint.side ===
								'left'
									? '-left-[3px]'
									: '-right-[3px]'}"
								aria-hidden="true"
							></span>
						{/if}
						<div class="flex items-start gap-2">
							<GripVerticalIcon class="text-muted-foreground mt-0.5 size-4 shrink-0 cursor-grab" />
							<div class="min-w-0 flex-1">
								<p class="truncate text-sm font-medium">{field.label || '(untitled)'}</p>
								<p class="text-muted-foreground truncate font-mono text-xs">
									{field.fieldKey || '—'}
								</p>
							</div>
							<div class="flex shrink-0 items-center gap-1">
								<Button
									variant="ghost"
									size="icon"
									class="size-7"
									aria-label="Edit field"
									onclick={() => openEdit(field)}
								>
									<PencilIcon class="size-4" />
								</Button>
								<Button
									variant="ghost"
									size="icon"
									class="text-muted-foreground hover:text-destructive size-7"
									aria-label="Delete field"
									onclick={() => requestRemoveField(field)}
								>
									<Trash2Icon class="size-4" />
								</Button>
							</div>
						</div>
						<div class="flex flex-wrap items-center gap-1.5">
							<Badge variant="outline" class="shrink-0">{typeLabel(field.fieldType)}</Badge>
							{#if field.required}<StatusBadge tone="success">Required</StatusBadge>{/if}
							{#if field.conditions}<Badge variant="outline">Conditional</Badge>{/if}
						</div>

						<!-- Divider handle: drag to shift columns between this field and the next. Hidden on
						     mobile (single column) and for the last field in a section (no neighbour). -->
						{#if fi < sec.fields.length - 1}
							<button
								type="button"
								aria-label="Drag to resize"
								title="Drag to share this row with the next field"
								class="hover:bg-ring/60 focus-visible:bg-ring absolute inset-y-1 -right-[5px] z-10 hidden w-2 cursor-col-resize touch-none rounded-full bg-transparent transition-colors md:block"
								onpointerdown={(e) => startResize(e, sec.fields, fi)}
							></button>
						{/if}
					</div>
				{:else}
					<p class="text-muted-foreground col-span-full px-1 py-3 text-center text-sm">
						No fields in this section yet.
					</p>
				{/each}

				{#if dropSectionId === sec.id && dragFieldUid}
					<!-- Full-row insertion line: previews "dropped here → its own row at the end". -->
					<div class="bg-primary col-span-full h-1 rounded-full" aria-hidden="true"></div>
				{/if}

				<Button
					variant="ghost"
					size="sm"
					class="text-muted-foreground col-span-full justify-self-start"
					onclick={() => openNew(sec.id)}
				>
					<PlusIcon class="mr-1 size-4" /> Add field
				</Button>
			</div>
		</div>

		{#if si < sections.length - 1}
			<!-- Insert a section between two existing ones. -->
			<div class="group relative flex items-center justify-center py-0.5">
				<div
					class="bg-border absolute inset-x-0 top-1/2 h-px opacity-0 transition-opacity group-hover:opacity-100"
				></div>
				<button
					type="button"
					class="bg-background text-muted-foreground hover:text-foreground hover:border-ring relative flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs opacity-60 transition-all group-hover:opacity-100"
					aria-label="Insert section here"
					onclick={() => insertSection(si + 1)}
				>
					<PlusIcon class="size-3.5" /> Section
				</button>
			</div>
		{/if}
	{/each}

	<div class="flex justify-center pt-1">
		<Button
			variant="ghost"
			size="sm"
			class="text-muted-foreground"
			onclick={() => insertSection(sections.length)}
		>
			<PlusIcon class="mr-1.5 size-4" /> Add section
		</Button>
	</div>

	<ScoringPanel
		fields={sections.flatMap((section) => section.fields)}
		{scoring}
		onchange={onscoring}
	/>
</div>

<FieldEditorSheet
	bind:open={sheetOpen}
	field={editing}
	siblingFields={sections.flatMap((s) => s.fields)}
	onsave={handleSave}
/>

<ConfirmDialog
	bind:open={fieldDeleteOpen}
	title="Delete this field?"
	description={pendingFieldDelete
		? `“${pendingFieldDelete.label || pendingFieldDelete.fieldKey || 'This field'}” will be removed from the form.`
		: undefined}
	confirmLabel="Delete"
	destructive
	onconfirm={() => {
		if (pendingFieldDelete) removeField(pendingFieldDelete.uid);
	}}
/>
