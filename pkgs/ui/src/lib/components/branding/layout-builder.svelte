<script lang="ts">
	import { untrack } from 'svelte';
	import { dndzone, type DndEvent } from 'svelte-dnd-action';
	import { flip } from 'svelte/animate';
	import type { BrandingBlock } from '@graphene/auth';
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import XIcon from '@lucide/svelte/icons/x';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import { Switch } from '../ui/switch/index.js';
	import { Button } from '../ui/button/index.js';
	import {
		CLIENT_BLOCKS,
		CLIENT_BLOCK_BY_TYPE,
		MAX_COLS,
		MAX_ROWS,
		clampBlockSpan,
		minBlockW
	} from './client-blocks.js';
	import type { BlockType } from './client-blocks.js';
	import { mintBlockId } from './portal-pages.js';
	import { layoutToRows, rowsToLayout, rowSignature, type LayoutRow } from './pages-view.js';
	import {
		authoredSpan,
		builderGeometry,
		NO_METRICS,
		ROWS_PER_UNIT,
		tileSpan,
		type PortalGridMetrics
	} from '../investment/masonry.js';
	import { cn } from '../../utils.js';

	// The visual page-layout builder over a 12-column grid. Drag a tile to MOVE it (reorders
	// the grid), drag its corner handle or use the ± steppers to RESIZE its column/row
	// span, toggle each tile to ENABLE/DISABLE it, and click a tile (anywhere but its controls) to
	// SELECT it for the inspector. Every committed change emits a fresh BrandingBlock[] via
	// `onchange` — the parent owns the value. Read-only when !canManage (selection still works so
	// a read-only viewer can inspect a block's settings). The row↔block mapping lives in
	// pages-view.ts so the emit contract is unit-tested. The tiles are a scaled miniature of the real
	// page, drawn in the portal's own unit and flow from `metrics`; the span maths is in masonry.ts.
	let {
		layout,
		canManage = false,
		selectedId = null,
		metrics = null,
		onchange,
		onselect
	}: {
		layout: BrandingBlock[];
		canManage?: boolean;
		/** What the live preview measured (PortalPage's `onmeasure`). Absent, or a block it has not reported (just dragged in, hidden), falls back to the authored `h`. */
		metrics?: PortalGridMetrics | null;
		/** The inspector-selected block's id — highlights its tile. */
		selectedId?: string | null;
		onchange?: (next: BrandingBlock[]) => void;
		/** Tile click (not the grip/switch/steppers/corner). Clicking the selected tile again
		 * reports null (deselect). Absent ⇒ tiles are not selectable (legacy consumers). */
		onselect?: (id: string | null) => void;
	} = $props();

	type Row = LayoutRow;

	let rows = $state<Row[]>(untrack(() => layoutToRows(layout)));

	// Re-seed when the parent hands us a different layout (compared by a cheap signature so our own
	// emits, echoed back, don't clobber an in-flight interaction). Settings are part of the
	// signature: the inspector edits them on the parent's copy, and without them here that edit
	// would never re-seed the tiles.
	let lastSig = $state(untrack(() => rowSignature(layoutToRows(layout))));
	$effect(() => {
		const sig = rowSignature(layoutToRows(layout));
		if (sig !== lastSig) {
			lastSig = sig;
			rows = layoutToRows(layout);
		}
	});

	function labelFor(type: string): string {
		return CLIENT_BLOCK_BY_TYPE[type]?.label ?? type;
	}

	function emit(next: Row[]) {
		lastSig = rowSignature(next);
		onchange?.(rowsToLayout(next));
	}

	// Drag only starts from the grip handle (not the whole tile) so clicking the switch / steppers /
	// resize handle never accidentally drags. svelte-dnd-action's `dragDisabled` is toggled off on the
	// grip's pointerdown and back on once the gesture ends.
	let dragDisabled = $state(true);
	function startDrag(e: PointerEvent) {
		if (!canManage) return;
		if (e.pointerType === 'mouse' && e.button !== 0) return;
		dragDisabled = false;
	}
	function handleConsider(e: CustomEvent<DndEvent<Row>>) {
		rows = e.detail.items;
	}
	function handleFinalize(e: CustomEvent<DndEvent<Row>>) {
		rows = e.detail.items;
		dragDisabled = true;
		emit(rows);
	}

	function toggle(id: string, visible: boolean) {
		rows = rows.map((r) => (r.id === id ? { ...r, visible } : r));
		emit(rows);
	}
	function remove(id: string) {
		rows = rows.filter((r) => r.id !== id);
		emit(rows);
	}
	function add(type: BlockType) {
		const meta = CLIENT_BLOCK_BY_TYPE[type];
		const id = mintBlockId(type, new Set(rows.map((r) => r.id)));
		// Defaults are all ≥ minW today, but clamp anyway so a future default edit can't sneak a
		// block in below its own floor.
		const span = clampBlockSpan(type, meta?.defaultW ?? 12, meta?.defaultH ?? 1);
		rows = [...rows, { id, type, visible: true, ...span, settings: undefined }];
		emit(rows);
	}
	// Every resize path (steppers, corner drag, add) funnels through clampBlockSpan, so a block
	// can never be authored below its type's usable floor (BlockMeta.minW) — the root cause of
	// the "desktop table crushed into 350px" defects was layouts the builder happily emitted.
	function resize(id: string, w: number, h: number, commit = true) {
		rows = rows.map((r) => (r.id === id ? { ...r, ...clampBlockSpan(r.type, w, h) } : r));
		if (commit) emit(rows);
	}

	// ── The miniature: the portal's micro-rows, scaled, and row-gap 0 with the rhythm carried per
	// tile. SPARSE flow like the portal — dense back-fills holes sized by the client's own data ──
	let gridEl = $state<HTMLElement>();
	let gridWidth = $state(0);
	const m = $derived(metrics ?? NO_METRICS);
	const spans = $derived(rows.map((r) => tileSpan(r, m)));
	const geometry = $derived(builderGeometry(gridWidth, m, spans));
	/** Tiles taller than their authored floor — said on the tile, since H looks inert on them. */
	const fluid = $derived(rows.map((r, i) => (spans[i] ?? 0) > authoredSpan(r.h, m.gapPx, m.rowPx)));

	let resizing: {
		id: string;
		startX: number;
		startY: number;
		startW: number;
		startH: number;
		cellW: number;
		rowH: number;
	} | null = null;
	const GAP = 12; // gap-x-3

	function startResize(e: PointerEvent, row: Row) {
		if (!canManage || !gridEl) return;
		e.stopPropagation();
		e.preventDefault();
		const width = gridEl.getBoundingClientRect().width;
		const cellW = (width - GAP * (MAX_COLS - 1)) / MAX_COLS + GAP;
		resizing = {
			id: row.id,
			startX: e.clientX,
			startY: e.clientY,
			startW: row.w,
			startH: row.h,
			cellW,
			// One unit of authored `h` at the current scale: dragging is a no-op until the floor clears the block's measured height.
			rowH: ROWS_PER_UNIT * geometry.rowPx
		};
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
	}
	function moveResize(e: PointerEvent) {
		if (!resizing) return;
		const dCols = Math.round((e.clientX - resizing.startX) / resizing.cellW);
		const dRows = Math.round((e.clientY - resizing.startY) / resizing.rowH);
		resize(resizing.id, resizing.startW + dCols, resizing.startH + dRows, false);
	}
	function endResize() {
		if (!resizing) return;
		resizing = null;
		emit(rows);
	}

	const dndOptions = $derived({
		items: rows,
		flipDurationMs: 160,
		dragDisabled: !canManage || dragDisabled || resizing !== null,
		dropTargetStyle: {}
	});
</script>

<svelte:window onpointerup={() => (dragDisabled = true)} />

<div class="space-y-4">
	{#if rows.length === 0}
		<p class="text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
			No blocks in the layout. Add one below to get started.
		</p>
	{:else}
		<div
			bind:this={gridEl}
			bind:clientWidth={gridWidth}
			class="grid grid-flow-row grid-cols-12 gap-x-3"
			style="grid-auto-rows: {geometry.rowPx}px; row-gap: 0"
			use:dndzone={dndOptions}
			onconsider={handleConsider}
			onfinalize={handleFinalize}
		>
			{#each rows as row, i (row.id)}
				<div
					animate:flip={{ duration: 160 }}
					data-block-id={row.id}
					style="grid-column: span {row.w}; grid-row: span {spans[
						i
					]}; margin-bottom: {geometry.gapPx}px"
					class={cn(
						'bg-card relative flex min-w-0 flex-col overflow-hidden rounded-lg border p-2.5',
						!row.visible && 'opacity-50',
						selectedId === row.id && 'border-primary ring-primary/40 ring-2'
					)}
				>
					{#if onselect}
						<!-- Selection backdrop: a real button under the controls (they lift above it with
						     relative z-10), so any click that ISN'T on the grip/switch/steppers/corner
						     selects the tile — and it stays focusable/announced, unlike a div onclick. -->
						<button
							type="button"
							class="focus-visible:ring-ring/50 absolute inset-0 rounded-lg focus-visible:ring-2 focus-visible:outline-none"
							aria-label={`Configure ${labelFor(row.type)}`}
							aria-pressed={selectedId === row.id}
							onclick={() => onselect?.(selectedId === row.id ? null : row.id)}
						></button>
					{/if}
					<div class="flex items-center gap-1.5">
						{#if canManage}
							<button
								type="button"
								class="text-muted-foreground hover:text-foreground relative z-10 -m-1 mt-0 shrink-0 touch-none cursor-grab p-1 active:cursor-grabbing"
								aria-label={`Move ${labelFor(row.type)}`}
								onpointerdown={startDrag}
							>
								<GripVerticalIcon class="size-4" />
							</button>
						{/if}
						<div class="min-w-0 flex-1">
							<p class="truncate text-xs font-semibold">{labelFor(row.type)}</p>
							<p class="text-muted-foreground text-[0.65rem] tabular-nums">
								{row.w}×{row.h}{fluid[i] ? ' · fits content' : ''}{!row.visible ? ' · hidden' : ''}
							</p>
						</div>
						{#if canManage}
							<Switch
								class="relative z-10 scale-75"
								checked={row.visible}
								aria-label={`${labelFor(row.type)} visible`}
								onCheckedChange={(v) => toggle(row.id, v)}
							/>
							<Button
								variant="ghost"
								size="icon"
								class="text-muted-foreground hover:text-destructive relative z-10 size-6 shrink-0"
								aria-label={`Remove ${labelFor(row.type)}`}
								onclick={() => remove(row.id)}
							>
								<XIcon class="size-3.5" />
							</Button>
						{/if}
					</div>

					{#if canManage}
						<!-- ± steppers (accessible resize) -->
						<div
							class="text-muted-foreground relative z-10 mt-auto flex items-center gap-2 text-[0.65rem]"
						>
							<div class="flex items-center gap-0.5">
								<span class="uppercase">W</span>
								<!-- Disabled at the type's minW floor, not 1 — the resize gate would refuse the
								     step anyway, and a live-looking button that does nothing reads as broken. -->
								<button
									type="button"
									class="hover:text-foreground disabled:opacity-30"
									aria-label={`${labelFor(row.type)} narrower`}
									disabled={row.w <= minBlockW(row.type)}
									onclick={() => resize(row.id, row.w - 1, row.h)}
								>
									<MinusIcon class="size-3" />
								</button>
								<button
									type="button"
									class="hover:text-foreground disabled:opacity-30"
									aria-label={`${labelFor(row.type)} wider`}
									disabled={row.w >= MAX_COLS}
									onclick={() => resize(row.id, row.w + 1, row.h)}
								>
									<PlusIcon class="size-3" />
								</button>
							</div>
							<!-- H is a minimum: most blocks already clear it, so the stepper reads as broken without the title. -->
							<div
								class="flex items-center gap-0.5"
								title="Minimum height — blocks grow to fit their content."
							>
								<span class="uppercase">H</span>
								<button
									type="button"
									class="hover:text-foreground disabled:opacity-30"
									aria-label={`${labelFor(row.type)} shorter`}
									disabled={row.h <= 1}
									onclick={() => resize(row.id, row.w, row.h - 1)}
								>
									<MinusIcon class="size-3" />
								</button>
								<button
									type="button"
									class="hover:text-foreground disabled:opacity-30"
									aria-label={`${labelFor(row.type)} taller`}
									disabled={row.h >= MAX_ROWS}
									onclick={() => resize(row.id, row.w, row.h + 1)}
								>
									<PlusIcon class="size-3" />
								</button>
							</div>
						</div>

						<!-- Corner drag handle -->
						<span
							role="slider"
							tabindex="-1"
							aria-label={`Resize ${labelFor(row.type)}`}
							aria-valuenow={row.w}
							aria-valuemin={minBlockW(row.type)}
							aria-valuemax={MAX_COLS}
							class="border-muted-foreground/50 absolute right-1 bottom-1 z-10 size-3 cursor-nwse-resize touch-none rounded-sm border-r-2 border-b-2"
							onpointerdown={(e) => startResize(e, row)}
							onpointermove={moveResize}
							onpointerup={endResize}
							onpointercancel={endResize}
						></span>
					{/if}
				</div>
			{/each}
		</div>
	{/if}

	{#if canManage}
		<div class="space-y-2">
			<p class="text-muted-foreground text-xs font-medium">Add a block</p>
			<!-- Every registry type, always — a page may carry several instances of the same block
			     (two message panels, say); each add mints a fresh per-page id via mintBlockId. -->
			<div class="flex flex-wrap gap-2">
				{#each CLIENT_BLOCKS as block (block.type)}
					<Button variant="outline" size="sm" class="gap-1.5" onclick={() => add(block.type)}>
						<PlusIcon class="size-3.5" />
						{block.label}
					</Button>
				{/each}
			</div>
		</div>
	{/if}
</div>
