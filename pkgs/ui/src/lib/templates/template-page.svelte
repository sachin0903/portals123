<script lang="ts">
	import {
		MM_TO_PX,
		type TemplateContent,
		type TemplatePage,
		type TemplateBlock
	} from './types.js';
	import { parseTokens, pageSizePx } from './helpers.js';

	let {
		content,
		page,
		selectable = false,
		selectedBlockUid = null,
		onselectblock
	}: {
		content: TemplateContent;
		page: TemplatePage;
		selectable?: boolean;
		selectedBlockUid?: string | null;
		onselectblock?: (uid: string) => void;
	} = $props();

	const MM = MM_TO_PX;
	const size = $derived(pageSizePx(content));

	const headingClass: Record<1 | 2 | 3, string> = {
		1: 'text-[26px] font-bold leading-tight',
		2: 'text-[17px] font-semibold',
		3: 'text-[13px] font-semibold'
	};

	function alignClass(a: TemplateBlock['align']): string {
		return a === 'center'
			? 'text-center'
			: a === 'right'
				? 'text-right'
				: a === 'justify'
					? 'text-justify'
					: 'text-left';
	}

	function typoClass(b: TemplateBlock): string {
		return [b.bold ? 'font-bold' : '', b.italic ? 'italic' : '', b.underline ? 'underline' : '']
			.filter(Boolean)
			.join(' ');
	}

	function typoStyle(b: TemplateBlock): string {
		let s = '';
		if (b.fontSize) s += `font-size:${b.fontSize}px;`;
		if (b.lineHeight) s += `line-height:${b.lineHeight};`;
		if (b.color) s += `color:${b.color};`;
		return s;
	}

	function lines(text?: string): string[] {
		return (text ?? '').split('\n').filter((l) => l.trim().length > 0);
	}

	function select(uid: string): void {
		onselectblock?.(uid);
	}

	const DEFAULT_TABLE = {
		cols: ['Item', 'Value'],
		rows: [
			['{{ row.label }}', '{{ row.value }}'],
			['Item two', '£1,250.00'],
			['Item three', '£980.00']
		]
	};
	const TABLES: Record<string, { cols: string[]; rows: string[][] }> = {
		data: DEFAULT_TABLE,
		holdings: {
			cols: ['Holding', 'Units', 'Value'],
			rows: [
				['{{ holding.name }}', '{{ holding.units }}', '{{ holding.value }}'],
				['Global Equity Fund', '1,204.5', '£18,400'],
				['UK Gilt Fund', '880.0', '£9,120']
			]
		},
		projections: {
			cols: ['Year', 'Low', 'Mid', 'High'],
			rows: [
				['5', '£11,200', '£12,800', '£14,600'],
				['10', '£12,500', '£16,400', '£21,300'],
				['15', '£14,000', '£21,000', '£31,000']
			]
		}
	};

	const BARS = [42, 68, 55, 82, 60, 74];
</script>

<!--
  Document preview canvas. This intentionally renders fixed print styling
  (white paper, ink text, brand teal) so it mirrors the generated PDF rather
  than the app's light/dark theme — it is document output, not app chrome.
-->
{#snippet toks(t: string)}
	{#each parseTokens(t) as seg, i (i)}
		{#if seg.token}<span class="font-medium text-teal-600">{seg.value}</span>{:else}{seg.value}{/if}
	{/each}
{/snippet}

{#snippet blockBody(block: TemplateBlock)}
	{#if block.type === 'image'}
		<div class="flex items-center gap-2.5 {alignClass(block.align)}">
			<div
				class="flex size-10 items-center justify-center rounded-lg"
				style="background:linear-gradient(135deg,#0f766e,#5eead4)"
			>
				<div class="size-4 rounded-sm bg-white/90"></div>
			</div>
			<span class="text-[20px] font-bold tracking-tight text-neutral-700">
				{block.label || 'Logo'}
			</span>
		</div>
	{:else if block.type === 'heading'}
		<p
			class="{headingClass[block.level ?? 2]} {alignClass(block.align)} {typoClass(block)}"
			style={typoStyle(block)}
		>
			{@render toks(block.text ?? '')}
		</p>
	{:else if block.type === 'paragraph'}
		<p
			class="text-[13px] leading-relaxed {alignClass(block.align)} {typoClass(block)}"
			style={typoStyle(block)}
		>
			{@render toks(block.text ?? '')}
		</p>
	{:else if block.type === 'labelValue'}
		<div class="flex items-baseline justify-between gap-3 text-[13px]">
			<span class="font-semibold">{block.label || 'Label'}</span>
			<span class="text-right">{@render toks(block.text ?? '')}</span>
		</div>
	{:else if block.type === 'bulletList'}
		<ul class="list-disc space-y-0.5 pl-5 text-[13px]">
			{#each lines(block.text) as li, i (i)}
				<li>{@render toks(li)}</li>
			{/each}
		</ul>
	{:else if block.type === 'numberedList'}
		<ol class="list-decimal space-y-0.5 pl-5 text-[13px]">
			{#each lines(block.text) as li, i (i)}
				<li>{@render toks(li)}</li>
			{/each}
		</ol>
	{:else if block.type === 'divider'}
		<hr class="border-neutral-300" />
	{:else if block.type === 'noteBox'}
		<div
			class="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-[12px] text-neutral-700"
		>
			{#if block.label}<p class="mb-0.5 font-semibold">{block.label}</p>{/if}
			{@render toks(block.text ?? '')}
		</div>
	{:else if block.type === 'highlightBox'}
		<div class="rounded-md border border-teal-200 bg-teal-50 px-3 py-2 text-[12px] text-teal-900">
			{#if block.label}<p class="mb-0.5 font-semibold">{block.label}</p>{/if}
			{@render toks(block.text ?? '')}
		</div>
	{:else if block.type === 'spacer'}
		<div style="height:{block.height ?? 24}px"></div>
	{:else if block.type === 'columns'}
		<div class="grid gap-2 {block.variant === 'two' ? 'grid-cols-2' : 'grid-cols-1'}">
			{#each [...Array(block.variant === 'two' ? 2 : 1).keys()] as i (i)}
				<div
					class="flex h-14 items-center justify-center rounded border border-dashed border-neutral-300 text-[11px] text-neutral-400"
				>
					Column {i + 1}
				</div>
			{/each}
		</div>
	{:else if block.type === 'chart'}
		<div class="rounded-md border border-neutral-200 p-3">
			{#if block.label}<p class="mb-2 text-[12px] font-semibold text-neutral-700">
					{block.label}
				</p>{/if}
			{#if block.variant === 'pie'}
				<div
					class="mx-auto size-24 rounded-full"
					style="background:conic-gradient(#0f766e 0 45%, #5eead4 45% 70%, #a7f3d0 70% 88%, #cbd5e1 88% 100%)"
				></div>
			{:else if block.variant === 'line'}
				<svg viewBox="0 0 200 80" class="h-20 w-full">
					<polyline
						points="0,62 33,48 66,54 100,30 133,38 166,16 200,22"
						fill="none"
						stroke="#0f766e"
						stroke-width="2"
					/>
					<polygon
						points="0,62 33,48 66,54 100,30 133,38 166,16 200,22 200,80 0,80"
						fill="#0f766e22"
					/>
				</svg>
			{:else}
				<div class="flex h-20 items-end gap-1.5">
					{#each BARS as b, i (i)}
						<div class="flex-1 rounded-t bg-teal-500/70" style="height:{b}%"></div>
					{/each}
				</div>
			{/if}
		</div>
	{:else if block.type === 'table'}
		{@const tbl = TABLES[block.variant ?? 'data'] ?? DEFAULT_TABLE}
		<table class="w-full border-collapse text-[12px]">
			<thead>
				<tr class="border-b border-neutral-300 text-left">
					{#each tbl.cols as c (c)}
						<th class="px-2 py-1.5 font-semibold text-neutral-700">{c}</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each tbl.rows as row, ri (ri)}
					<tr class="border-b border-neutral-200">
						{#each row as cell, ci (ci)}
							<td class="px-2 py-1.5">{@render toks(cell)}</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	{:else if block.type === 'pageBreak'}
		<div class="flex items-center gap-2 text-[10px] uppercase tracking-wide text-neutral-400">
			<span class="h-px flex-1 border-t border-dashed border-neutral-300"></span>
			Page break
			<span class="h-px flex-1 border-t border-dashed border-neutral-300"></span>
		</div>
	{:else if block.type === 'conditional'}
		<div
			class="rounded border border-dashed border-amber-300 bg-amber-50/60 px-3 py-1.5 font-mono text-[11px] text-amber-800"
		>
			if {block.text || 'condition'} …
		</div>
	{:else if block.type === 'repeat'}
		<div
			class="rounded border border-dashed border-indigo-300 bg-indigo-50/60 px-3 py-1.5 font-mono text-[11px] text-indigo-800"
		>
			for each {block.text || 'collection'} …
		</div>
	{/if}
{/snippet}

<div
	class="relative shrink-0 bg-white text-neutral-900 shadow-lg"
	style="width:{size.w}px; height:{size.h}px; padding:{content.margins.top * MM}px {content.margins
		.right * MM}px {content.margins.bottom * MM}px {content.margins.left * MM}px;
		{content.border !== 'none' ? `border:1.5px ${content.border} #cbd5e1;` : ''}
		{content.background === 'color' ? 'background-color:#f8fafc;' : ''}"
>
	{#if page.blocks.length === 0}
		{#if selectable}
			<div
				class="flex h-full items-center justify-center rounded-md border-2 border-dashed border-neutral-200"
			>
				<p class="text-[13px] text-neutral-400">
					Empty page — add blocks from the <span class="font-medium">Blocks</span> panel
				</p>
			</div>
		{/if}
	{:else}
		<div class="space-y-[10px]">
			{#each page.blocks as block (block.uid)}
				{#if selectable}
					<div
						class={[
							'relative cursor-pointer rounded-[3px] outline-offset-2 transition-shadow hover:outline hover:outline-1 hover:outline-teal-400/60',
							block.uid === selectedBlockUid ? 'outline outline-2 outline-teal-500' : ''
						].join(' ')}
						role="button"
						tabindex={0}
						onclick={(e) => {
							e.stopPropagation();
							select(block.uid);
						}}
						onkeydown={(e) => {
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								select(block.uid);
							}
						}}
					>
						{@render blockBody(block)}
					</div>
				{:else}
					<div class="relative">{@render blockBody(block)}</div>
				{/if}
			{/each}
		</div>
	{/if}
</div>
