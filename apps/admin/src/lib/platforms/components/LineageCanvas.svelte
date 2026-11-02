<script lang="ts">
	// Column-level lineage DAG — DOM nodes + one SVG edge layer inside a
	// pan/zoom viewport. Everything is derived reactively from the store, so a
	// reloaded dataset re-lays-out in place (no imperative canvas state to go
	// stale) and hit-testing is native DOM. Colours come straight from the
	// theme's CSS variables.
	import { untrack } from 'svelte';
	import { MAT_GLYPH } from '../status';
	import type { PlatformStore } from '../store.svelte';
	import type { ChangeState } from '../types';

	let {
		store,
		/** Tailwind height class for the viewport. The graph is worth as much room as the page can
		 *  give it, so the dedicated Lineage section passes a viewport-relative height. */
		heightClass = 'h-[560px]'
	}: { store: PlatformStore; heightClass?: string } = $props();

	// ── layout constants ──
	const NODE_W = 200;
	const HEADER_H = 40;
	const ROW_H = 19;
	const COLLAPSED_H = 52;
	const LAYER_GAP = 84;
	const V_GAP = 18;
	const PAD = 32;
	// Wide relations (raw CRM mirrors carry hundreds of columns) cap their
	// expansion; the Models tab lists the full schema.
	const MAX_COL_ROWS = 24;

	const STATE_COLOUR: Record<ChangeState, string> = {
		added: 'var(--positive)',
		breaking: 'var(--destructive)',
		nonbreaking: 'var(--brand)',
		unchanged: 'var(--muted-foreground)'
	};

	interface ColRow {
		out: string;
		srcModel: string;
		srcCol: string;
		y: number; // world-space centre of the row
	}
	interface NodeBox {
		name: string;
		x: number;
		y: number;
		w: number;
		h: number;
		open: boolean;
		rows: ColRow[];
		hidden: number; // columns beyond the cap
	}

	// ── layout (pure, from store state) ──
	const layout = $derived.by(() => {
		const models = store.data.models;
		const byLayer = new Map<number, typeof models>();
		for (const m of models) {
			if (!byLayer.has(m.layer)) byLayer.set(m.layer, []);
			byLayer.get(m.layer)!.push(m);
		}
		const layers = [...byLayer.keys()].sort((a, b) => a - b);
		const boxes = new Map<string, NodeBox>();
		const captions: { label: string; x: number }[] = [];
		let maxY = 0;

		for (const [index, layer] of layers.entries()) {
			const column = byLayer.get(layer)!;
			const x = PAD + index * (NODE_W + LAYER_GAP);
			const label = column.map((m) => m.layerLabel).find((l): l is string => !!l);
			if (label) captions.push({ label, x: x + NODE_W / 2 });
			// Anchor from the collapsed heights so expanding grows downward without
			// re-centring — nodes must not move away from the pointer mid-interaction.
			let y = PAD + 14;
			for (const m of column) {
				const open = store.expanded.has(m.name) && m.cols.length > 0;
				const shown = open ? m.cols.slice(0, MAX_COL_ROWS) : [];
				const hidden = open ? m.cols.length - shown.length : 0;
				const h = open ? HEADER_H + (shown.length + (hidden ? 1 : 0)) * ROW_H + 8 : COLLAPSED_H;
				boxes.set(m.name, {
					name: m.name,
					x,
					y,
					w: NODE_W,
					h,
					open,
					hidden,
					rows: shown.map((c, i) => ({
						out: c.out,
						srcModel: c.srcModel,
						srcCol: c.srcCol,
						y: y + HEADER_H + i * ROW_H + ROW_H / 2
					}))
				});
				y += h + V_GAP;
				maxY = Math.max(maxY, y);
			}
		}
		const width = PAD * 2 + layers.length * NODE_W + (layers.length - 1) * LAYER_GAP;
		return { boxes, captions, width: Math.max(width, 400), height: Math.max(maxY + PAD, 300) };
	});

	// ── edges ──
	interface Edge {
		id: string;
		d: string;
		lit: boolean; // on the selected model's path
		hot: boolean; // on the hovered column's thread
	}

	let hover = $state<{ node: string; col: string } | null>(null);

	const onPath = $derived.by(() => {
		const sel = store.byName.get(store.selected);
		return new Set(sel ? [sel.name, ...sel.deps, ...(store.downstream.get(sel.name) ?? [])] : []);
	});

	const edges = $derived.by(() => {
		const out: Edge[] = [];
		const { boxes } = layout;
		const curve = (x1: number, y1: number, x2: number, y2: number) => {
			const mx = (x1 + x2) / 2;
			return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
		};
		const outAnchor = (box: NodeBox, col: string | null) => {
			const row = col ? box.rows.find((r) => r.out === col) : undefined;
			return {
				x: box.x + box.w,
				y: row ? row.y : box.open ? box.y + HEADER_H / 2 : box.y + box.h / 2
			};
		};
		const inAnchor = (box: NodeBox, col: string | null) => {
			const row = col ? box.rows.find((r) => r.out === col) : undefined;
			return { x: box.x, y: row ? row.y : box.open ? box.y + HEADER_H / 2 : box.y + box.h / 2 };
		};
		for (const m of store.data.models) {
			const target = boxes.get(m.name);
			if (!target) continue;
			for (const dep of m.deps) {
				const source = boxes.get(dep);
				if (!source) continue;
				const lit = onPath.has(m.name) && onPath.has(dep);
				const colEdges = target.open || source.open ? m.cols.filter((c) => c.srcModel === dep) : [];
				if (!colEdges.length) {
					const a = outAnchor(source, null);
					const b = inAnchor(target, null);
					out.push({ id: `${dep}->${m.name}`, d: curve(a.x, a.y, b.x, b.y), lit, hot: false });
					continue;
				}
				for (const c of colEdges) {
					const a = outAnchor(source, c.srcCol);
					const b = inAnchor(target, c.out);
					const hot =
						hover !== null &&
						((hover.node === m.name && hover.col === c.out) ||
							(hover.node === dep && hover.col === c.srcCol));
					out.push({
						id: `${dep}.${c.srcCol}->${m.name}.${c.out}`,
						d: curve(a.x, a.y, b.x, b.y),
						lit,
						hot
					});
				}
			}
		}
		return out;
	});

	// ── pan / zoom ──
	let viewport = $state<HTMLDivElement>();
	let pan = $state({ x: 0, y: 0 });
	let zoom = $state(1);
	let fitted = false;
	let drag = $state<{ startX: number; startY: number; panX: number; panY: number } | null>(null);

	function fit() {
		const el = viewport;
		if (!el) return;
		const rect = el.getBoundingClientRect();
		const scale = Math.min(rect.width / layout.width, rect.height / layout.height, 1.15);
		zoom = Math.max(scale, 0.3);
		pan = {
			x: (rect.width - layout.width * zoom) / 2,
			y: Math.max((rect.height - layout.height * zoom) / 2, 0)
		};
	}

	// Fit once when the viewport first has a size, and again when the STORE is
	// swapped (slug change) — live data refreshes keep the user's pan/zoom.
	let lastStore: PlatformStore | null = null;
	$effect(() => {
		if (store !== lastStore) {
			lastStore = store;
			fitted = false;
		}
		void layout.width;
		if (!fitted && viewport) {
			fit();
			fitted = true;
		}
	});

	function zoomAt(clientX: number, clientY: number, factor: number) {
		const rect = viewport!.getBoundingClientRect();
		const mx = clientX - rect.left;
		const my = clientY - rect.top;
		const next = Math.min(2.5, Math.max(0.25, zoom * factor));
		pan = { x: mx - ((mx - pan.x) / zoom) * next, y: my - ((my - pan.y) / zoom) * next };
		zoom = next;
	}

	function onWheel(e: WheelEvent) {
		// Zoom only on ctrl/cmd+wheel (pinch sets ctrlKey) — plain wheel scrolls the page.
		if (!e.ctrlKey && !e.metaKey) return;
		e.preventDefault();
		zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.12 : 1 / 1.12);
	}

	function onPointerDown(e: PointerEvent) {
		if (e.button !== 0) return;
		drag = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
	}
	function onPointerMove(e: PointerEvent) {
		if (!drag) return;
		pan = { x: drag.panX + (e.clientX - drag.startX), y: drag.panY + (e.clientY - drag.startY) };
	}
	function onPointerUp() {
		drag = null;
	}

	// Search on the lineage page calls `focus`, which bumps this tick. Centre the node in the
	// viewport — scrollIntoView cannot see through the pan/zoom transform.
	$effect(() => {
		const tick = store.focusTick;
		if (!tick || !viewport) return;
		const name = untrack(() => store.selected);
		const box = untrack(() => layout.boxes.get(name));
		const scale = untrack(() => zoom);
		if (!box) return;
		const rect = viewport.getBoundingClientRect();
		pan = {
			x: rect.width / 2 - (box.x + box.w / 2) * scale,
			y: rect.height / 2 - (box.y + box.h / 2) * scale
		};
	});

	const centreButtons = [
		{ label: '+', action: () => zoomButton(1.25) },
		{ label: '−', action: () => zoomButton(1 / 1.25) },
		{ label: '⤢', action: fit }
	];
	function zoomButton(factor: number) {
		const rect = viewport!.getBoundingClientRect();
		zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
	}
</script>

<div
	bind:this={viewport}
	class={`bg-card/50 group relative w-full touch-none overflow-hidden rounded-xl border ${heightClass}`}
	class:cursor-grabbing={drag !== null}
	class:cursor-grab={drag === null}
	role="application"
	aria-label="Model lineage graph"
	onwheel={onWheel}
	onpointerdown={onPointerDown}
	onpointermove={onPointerMove}
	onpointerup={onPointerUp}
	onpointercancel={onPointerUp}
>
	<div
		class="absolute top-0 left-0 origin-top-left will-change-transform"
		style="transform: translate({pan.x}px, {pan.y}px) scale({zoom}); width: {layout.width}px; height: {layout.height}px"
	>
		<!-- edges -->
		<svg
			class="pointer-events-none absolute top-0 left-0"
			width={layout.width}
			height={layout.height}
			aria-hidden="true"
		>
			{#each edges as edge (edge.id)}
				<path
					d={edge.d}
					fill="none"
					stroke={edge.hot ? 'var(--brand)' : 'var(--primary)'}
					stroke-width={edge.hot ? 2.6 : edge.lit ? 2.2 : 1.4}
					opacity={edge.hot ? 1 : edge.lit ? 0.85 : 0.15}
				/>
			{/each}
		</svg>

		<!-- layer captions -->
		{#each layout.captions as caption (caption.x)}
			<div
				class="text-muted-foreground/80 absolute -translate-x-1/2 font-mono text-[11px] font-semibold select-none"
				style="left: {caption.x}px; top: 8px"
			>
				{caption.label}
			</div>
		{/each}

		<!-- nodes -->
		{#each store.data.models as m (m.name)}
			{@const box = layout.boxes.get(m.name)}
			{#if box}
				{@const selected = store.selected === m.name}
				{@const dim = onPath.size > 0 && !onPath.has(m.name)}
				<div
					class="bg-card absolute rounded-lg border text-left shadow-sm transition-[opacity,border-color] duration-150"
					data-model={m.name}
					class:border-primary={selected}
					class:opacity-45={dim}
					style="left: {box.x}px; top: {box.y}px; width: {box.w}px; height: {box.h}px"
					role="button"
					tabindex="0"
					onpointerdown={(e) => e.stopPropagation()}
					onclick={() => store.select(m.name)}
					ondblclick={() => m.cols.length && store.toggle(m.name)}
					onkeydown={(e) => e.key === 'Enter' && store.select(m.name)}
				>
					<!-- state / running bar -->
					<span
						class="absolute top-0 left-0 h-full w-1 rounded-l-lg"
						class:animate-pulse={m.running}
						style="background: {m.running ? 'var(--ring)' : STATE_COLOUR[m.state]}"
					></span>

					<div class="flex items-start justify-between gap-1 py-2 pr-2 pl-3">
						<div class="min-w-0">
							<div class="truncate font-mono text-xs font-semibold" title={m.name}>{m.name}</div>
							<div class="text-muted-foreground truncate font-mono text-[10px]">
								{MAT_GLYPH[m.mat]}
								{m.mat} · {m.strategy}
							</div>
						</div>
						{#if m.cols.length}
							<button
								type="button"
								class="text-muted-foreground hover:bg-accent hover:text-foreground mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border text-[11px] leading-none"
								title={box.open ? 'Collapse columns' : `Expand ${m.cols.length} columns`}
								aria-label={box.open ? 'Collapse columns' : 'Expand columns'}
								onclick={(e) => {
									e.stopPropagation();
									store.toggle(m.name);
								}}
							>
								{box.open ? '−' : '+'}
							</button>
						{/if}
					</div>

					{#if box.open}
						<div class="border-t"></div>
						<div class="py-1">
							{#each box.rows as row (row.out)}
								{@const hot = hover?.node === m.name && hover?.col === row.out}
								<!-- svelte-ignore a11y_no_static_element_interactions -->
								<div
									class="flex h-[19px] items-center gap-1.5 px-3 font-mono text-[10.5px]"
									class:bg-primary10={hot}
									onmouseenter={() => (hover = { node: m.name, col: row.out })}
									onmouseleave={() => (hover = null)}
								>
									<span
										class="truncate"
										class:text-foreground={hot}
										class:text-muted-foreground={!hot}
										title={row.srcCol === '—'
											? row.out
											: `${row.out} ← ${row.srcModel}.${row.srcCol}`}
									>
										{row.out}
									</span>
								</div>
							{/each}
							{#if box.hidden}
								<div class="text-muted-foreground/70 px-3 font-mono text-[10px] italic">
									+{box.hidden} more…
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/if}
		{/each}
	</div>

	<!-- controls -->
	<div class="absolute top-2 left-2 flex flex-col gap-1">
		{#each centreButtons as button (button.label)}
			<button
				type="button"
				class="bg-card text-muted-foreground hover:text-foreground flex size-6 items-center justify-center rounded-md border text-sm shadow-sm"
				onclick={button.action}
				aria-label={button.label === '⤢'
					? 'Fit to view'
					: button.label === '+'
						? 'Zoom in'
						: 'Zoom out'}
			>
				{button.label}
			</button>
		{/each}
	</div>

	<div
		class="text-muted-foreground pointer-events-none absolute bottom-2 left-3 text-[11px] select-none"
	>
		⊕ or double-click a model to expand columns · hover a column to trace its lineage · drag to pan
		· ctrl+scroll to zoom
	</div>
	<div
		class="text-muted-foreground pointer-events-none absolute top-2 right-3 flex items-center gap-3 text-[11px] select-none"
	>
		<span class="flex items-center gap-1">
			<i class="size-2 rounded-full" style="background: var(--positive)"></i>added
		</span>
		<span class="flex items-center gap-1">
			<i class="size-2 rounded-full" style="background: var(--destructive)"></i>rebuild
		</span>
		<span class="flex items-center gap-1">
			<i class="size-2 rounded-full" style="background: var(--brand)"></i>refresh
		</span>
		<span class="flex items-center gap-1">
			<i class="size-2 rounded-full" style="background: var(--muted-foreground)"></i>unchanged
		</span>
		<span class="flex items-center gap-1">
			<i class="size-2 rounded-full" style="background: var(--ring)"></i>running
		</span>
	</div>
</div>

<style>
	.bg-primary10 {
		background: color-mix(in oklab, var(--primary) 12%, transparent);
	}
</style>
