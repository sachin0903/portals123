<script lang="ts">
	import SelectMenu from '../components/ui/select-menu/select-menu.svelte';
	import { untrack } from 'svelte';
	import { Button } from '../components/ui/button/index.js';
	import { Switch } from '../components/ui/switch/index.js';
	import { Input } from '../components/ui/input/index.js';
	import { Label } from '../components/ui/label/index.js';
	import { Textarea } from '../components/ui/textarea/index.js';
	import { StatusBadge } from '../components/ui/status-badge/index.js';
	import * as Popover from '../components/ui/popover/index.js';
	import TemplatePageView from './template-page.svelte';
	import {
		TEMPLATE_STATUS_META,
		docTypeMeta,
		DATASET_FORMAT_META,
		PAGE_SIZES,
		BLOCK_LABELS,
		BLOCK_PALETTE,
		type Template,
		type Dataset,
		type TemplateContent,
		type TemplateStatus,
		type BlockType,
		type BlockAlign,
		type PageSize,
		type BorderStyle
	} from './types.js';
	import { pageSizePx, makeBlock, makePage } from './helpers.js';

	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import SaveIcon from '@lucide/svelte/icons/save';
	import CheckIcon from '@lucide/svelte/icons/check';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import PaletteIcon from '@lucide/svelte/icons/palette';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import BoldIcon from '@lucide/svelte/icons/bold';
	import ItalicIcon from '@lucide/svelte/icons/italic';
	import UnderlineIcon from '@lucide/svelte/icons/underline';
	import AlignLeftIcon from '@lucide/svelte/icons/align-left';
	import AlignCenterIcon from '@lucide/svelte/icons/align-center';
	import AlignRightIcon from '@lucide/svelte/icons/align-right';
	import AlignJustifyIcon from '@lucide/svelte/icons/align-justify';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import FileIcon from '@lucide/svelte/icons/file';
	import SlidersIcon from '@lucide/svelte/icons/sliders-horizontal';
	import PanelTopIcon from '@lucide/svelte/icons/panel-top';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import RectangleVerticalIcon from '@lucide/svelte/icons/rectangle-vertical';
	import RectangleHorizontalIcon from '@lucide/svelte/icons/rectangle-horizontal';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronUpIcon from '@lucide/svelte/icons/chevron-up';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	// Block-type icons
	import TypeIcon from '@lucide/svelte/icons/type';
	import ImageIcon from '@lucide/svelte/icons/image';
	import TagIcon from '@lucide/svelte/icons/tag';
	import ListIcon from '@lucide/svelte/icons/list';
	import ListOrderedIcon from '@lucide/svelte/icons/list-ordered';
	import StickyNoteIcon from '@lucide/svelte/icons/sticky-note';
	import SquareIcon from '@lucide/svelte/icons/square';
	import Columns2Icon from '@lucide/svelte/icons/columns-2';
	import BarChartIcon from '@lucide/svelte/icons/bar-chart-3';
	import LineChartIcon from '@lucide/svelte/icons/line-chart';
	import PieChartIcon from '@lucide/svelte/icons/pie-chart';
	import TableIcon from '@lucide/svelte/icons/table';
	import ScissorsIcon from '@lucide/svelte/icons/scissors';
	import GitBranchIcon from '@lucide/svelte/icons/git-branch';
	import RepeatIcon from '@lucide/svelte/icons/repeat';
	import HeadingIcon from '@lucide/svelte/icons/heading';
	import ArrowUpDownIcon from '@lucide/svelte/icons/arrow-up-down';

	let {
		template,
		initialContent,
		datasets = [],
		onsave,
		onpublish,
		onback
	}: {
		template: Template;
		initialContent: TemplateContent;
		datasets?: Dataset[];
		onsave?: (content: TemplateContent) => void;
		onpublish?: () => void;
		onback?: () => void;
	} = $props();

	function cloneContent(src: TemplateContent): TemplateContent {
		return JSON.parse(JSON.stringify(src)) as TemplateContent;
	}

	let content = $state<TemplateContent>(untrack(() => cloneContent(initialContent)));

	type LeftTab = 'pages' | 'layers' | 'blocks' | 'datasets';
	type RightTab = 'page' | 'component' | 'header' | 'details';

	let leftTab = $state<LeftTab>('blocks');
	let rightTab = $state<RightTab>('page');
	let pageIndex = $state(0);
	let selectedBlockUid = $state<string | null>(null);
	let zoom = $state(0.8);
	let preview = $state(false);
	let saved = $state(false);
	let status = $state<TemplateStatus>(untrack(() => template.currentVersionStatus ?? 'draft'));

	let themeFont = $state('Inter');
	let themeBase = $state(13);
	let brandColor = $state('#0f766e');
	let headingColor = $state('#0f172a');
	let openSections = $state<Record<string, boolean>>({ typography: true });
	function toggleSection(key: string) {
		openSections[key] = !openSections[key];
	}

	const stylePre = [
		{ key: 'layout', title: 'Layout', fields: ['Display', 'Direction', 'Align', 'Justify'] },
		{ key: 'size', title: 'Size', fields: ['Width', 'Height', 'Min W', 'Max W'] },
		{ key: 'space', title: 'Space', fields: ['Margin', 'Padding'] },
		{ key: 'position', title: 'Position', fields: ['Position', 'Top', 'Left'] }
	];
	const stylePost = [
		{ key: 'background', title: 'Background', fields: ['Colour', 'Image'] },
		{ key: 'borders', title: 'Borders', fields: ['Width', 'Style', 'Radius', 'Colour'] },
		{ key: 'effects', title: 'Effects', fields: ['Opacity', 'Shadow'] }
	];
	const historyItems = [
		{ action: 'Published version 2', who: 'James Okafor', when: '2 days ago' },
		{ action: 'Edited body copy', who: 'James Okafor', when: '5 days ago' },
		{ action: 'Added page 2', who: 'Priya Nair', when: '6 days ago' },
		{ action: 'Linked dataset: client_summary', who: 'Priya Nair', when: '1 week ago' },
		{ action: 'Created version 2 draft', who: 'Priya Nair', when: '1 week ago' }
	];

	const dtm = $derived(docTypeMeta(template.documentType));
	const safeIndex = $derived(Math.min(pageIndex, content.pages.length - 1));
	const currentPage = $derived(
		content.pages[safeIndex] ?? content.pages[0] ?? { uid: 'empty', blocks: [] }
	);
	const selectedBlock = $derived(
		currentPage.blocks.find((b) => b.uid === selectedBlockUid) ?? null
	);
	const sz = $derived(pageSizePx(content));
	const isEmpty = $derived(!content.pages.some((p) => p.blocks.length > 0));
	const linkedDatasets = $derived(
		template.linkedDatasets ? datasets.filter((d) => template.linkedDatasets!.includes(d.code)) : []
	);

	const leftTabs: { key: LeftTab; label: string }[] = [
		{ key: 'pages', label: 'Pages' },
		{ key: 'layers', label: 'Layers' },
		{ key: 'blocks', label: 'Blocks' },
		{ key: 'datasets', label: 'Datasets' }
	];
	const rightTabs: { key: RightTab; label: string }[] = [
		{ key: 'page', label: 'Page' },
		{ key: 'component', label: 'Component' },
		{ key: 'header', label: 'Header' },
		{ key: 'details', label: 'Details' }
	];

	function iconFor(type: BlockType, variant?: string): typeof FileIcon {
		switch (type) {
			case 'heading':
				return HeadingIcon;
			case 'paragraph':
				return TypeIcon;
			case 'image':
				return ImageIcon;
			case 'labelValue':
				return TagIcon;
			case 'bulletList':
				return ListIcon;
			case 'numberedList':
				return ListOrderedIcon;
			case 'divider':
				return MinusIcon;
			case 'noteBox':
				return StickyNoteIcon;
			case 'highlightBox':
				return SquareIcon;
			case 'spacer':
				return ArrowUpDownIcon;
			case 'columns':
				return variant === 'two' ? Columns2Icon : RectangleHorizontalIcon;
			case 'chart':
				return variant === 'line' ? LineChartIcon : variant === 'pie' ? PieChartIcon : BarChartIcon;
			case 'table':
				return TableIcon;
			case 'pageBreak':
				return ScissorsIcon;
			case 'conditional':
				return GitBranchIcon;
			case 'repeat':
				return RepeatIcon;
		}
	}

	function blockLabel(b: { type: BlockType; text?: string; label?: string }): string {
		if (b.type === 'image') return b.label || 'Logo';
		if (b.type === 'divider') return 'Divider';
		if (b.type === 'spacer') return 'Spacer';
		if (b.type === 'pageBreak') return 'Page break';
		if (b.label) return b.label;
		const t = (b.text ?? '').replace(/\n/g, ' ').trim();
		return t.length > 24 ? t.slice(0, 24) + '…' : t || BLOCK_LABELS[b.type];
	}

	function selectBlock(blockUid: string) {
		selectedBlockUid = blockUid;
		rightTab = 'component';
	}
	function deselect() {
		selectedBlockUid = null;
	}
	function addBlock(type: BlockType, variant?: string) {
		const block = makeBlock(type, variant);
		currentPage.blocks.push(block);
		selectBlock(block.uid);
	}
	function deleteBlock(blockUid: string) {
		currentPage.blocks = currentPage.blocks.filter((b) => b.uid !== blockUid);
		if (selectedBlockUid === blockUid) selectedBlockUid = null;
	}
	function moveBlock(blockUid: string, dir: -1 | 1) {
		const arr = currentPage.blocks;
		const i = arr.findIndex((b) => b.uid === blockUid);
		const j = i + dir;
		if (i < 0 || j < 0 || j >= arr.length) return;
		const a = arr[i];
		const b = arr[j];
		if (!a || !b) return;
		arr[i] = b;
		arr[j] = a;
	}
	function addPage() {
		content.pages.push(makePage());
		pageIndex = content.pages.length - 1;
		selectedBlockUid = null;
	}
	function deletePage(i: number) {
		if (content.pages.length <= 1) return;
		content.pages.splice(i, 1);
		pageIndex = Math.max(0, Math.min(pageIndex, content.pages.length - 1));
		selectedBlockUid = null;
	}
	function selectPage(i: number) {
		pageIndex = i;
		selectedBlockUid = null;
	}
	function setZoom(delta: number) {
		zoom = Math.min(1.5, Math.max(0.4, Math.round((zoom + delta) * 10) / 10));
	}
	function save() {
		onsave?.($state.snapshot(content));
		saved = true;
		setTimeout(() => (saved = false), 1800);
	}
	function publish() {
		if (isEmpty) return;
		onsave?.($state.snapshot(content));
		onpublish?.();
		status = 'published';
	}

	const borderStyles: { key: BorderStyle; label: string }[] = [
		{ key: 'none', label: 'None' },
		{ key: 'solid', label: 'Solid' },
		{ key: 'dashed', label: 'Dashed' },
		{ key: 'dotted', label: 'Dotted' }
	];
</script>

{#snippet mockSection(sec: { key: string; title: string; fields: string[] })}
	<div class="border-b">
		<button
			class="text-muted-foreground hover:text-foreground flex w-full items-center justify-between py-2.5 text-xs font-semibold"
			onclick={() => toggleSection(sec.key)}
		>
			{sec.title}
			<ChevronDownIcon
				class="size-3.5 transition-transform {openSections[sec.key] ? '' : '-rotate-90'}"
			/>
		</button>
		{#if openSections[sec.key]}
			<div class="grid grid-cols-2 gap-2 pb-3">
				{#each sec.fields as f (f)}
					<div class="space-y-1">
						<span class="text-muted-foreground text-[10px]">{f}</span>
						<input
							placeholder="inherit"
							class="border-input bg-background placeholder:text-muted-foreground/50 h-7 w-full rounded-md border px-2 text-xs"
						/>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

<!-- Outer is transparent so the side panels + workspace show the portal gradient
	 (glass). The two top bars (header + canvas toolbar) stay solid for legibility. -->
<div class="flex h-full flex-col overflow-hidden">
	<!-- Editor toolbar -->
	<header class="bg-card flex shrink-0 items-center gap-2 border-b px-3 py-1.5">
		<button
			class="hover:bg-accent text-muted-foreground hover:text-foreground flex size-7 shrink-0 items-center justify-center rounded-md transition-colors"
			title="Back to template"
			onclick={() => onback?.()}
		>
			<ArrowLeftIcon class="size-3.5" />
		</button>
		<div class="flex min-w-0 flex-1 items-center gap-1.5">
			<span class="text-foreground truncate text-xs font-medium">{template.name}</span>
			<StatusBadge tone={TEMPLATE_STATUS_META[status].tone} class="h-4 px-1.5 py-0 text-[10px]">
				{TEMPLATE_STATUS_META[status]
					.label}{#if template.currentVersionNumber}&nbsp;v{template.currentVersionNumber}{/if}
			</StatusBadge>
		</div>

		<div class="flex shrink-0 items-center gap-1.5">
			<label class="flex cursor-pointer items-center gap-1.5 select-none">
				<span class="text-muted-foreground text-xs">Preview</span>
				<Switch bind:checked={preview} onCheckedChange={() => (selectedBlockUid = null)} />
			</label>
			{#if status === 'published'}
				<Button size="sm" variant="outline" class="h-7 px-2 text-xs" disabled>
					<CheckIcon class="mr-1 size-3.5" />
					Published
				</Button>
			{:else}
				<Button
					size="sm"
					variant="outline"
					class="h-7 px-2 text-xs"
					onclick={publish}
					disabled={isEmpty}
					title={isEmpty ? 'Add content before publishing' : undefined}
				>
					<UploadIcon class="mr-1 size-3.5" />
					Publish
				</Button>
			{/if}
			<Button size="sm" class="h-7 px-2 text-xs" onclick={save}>
				{#if saved}
					<CheckIcon class="mr-1 size-3.5" />
					Saved
				{:else}
					<SaveIcon class="mr-1 size-3.5" />
					Save
				{/if}
			</Button>
		</div>
	</header>

	<div class="relative flex min-h-0 flex-1 overflow-hidden">
		<!-- Left panel -->
		{#if !preview}
			<aside class="glass-panel flex w-48 shrink-0 flex-col overflow-hidden border-r">
				<div class="bg-muted/40 flex shrink-0 border-b text-[10px]">
					{#each leftTabs as t (t.key)}
						<button
							class={[
								'relative flex-1 py-1.5 font-medium transition-colors',
								leftTab === t.key
									? 'text-foreground bg-card border-t-foreground -mb-px border-t-2'
									: 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
							].join(' ')}
							onclick={() => (leftTab = t.key)}
						>
							{t.label}
						</button>
					{/each}
				</div>

				<div class="min-h-0 flex-1 overflow-y-auto">
					{#if leftTab === 'pages'}
						<div class="p-3">
							<div class="mb-2 flex items-center justify-between">
								<p class="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase">
									Pages ({content.pages.length})
								</p>
								<button
									class="hover:bg-accent text-muted-foreground hover:text-foreground rounded p-1"
									title="Add page"
									onclick={addPage}
								>
									<PlusIcon class="size-3.5" />
								</button>
							</div>
							<div class="space-y-1.5">
								{#each content.pages as p, i (p.uid)}
									<div
										class={[
											'group flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs transition-colors',
											i === safeIndex
												? 'border-primary bg-primary/5'
												: 'hover:border-ring/50 hover:bg-muted/40 cursor-pointer'
										].join(' ')}
										role="button"
										tabindex={0}
										onclick={() => selectPage(i)}
										onkeydown={(e) => {
											if (e.key === 'Enter' || e.key === ' ') {
												e.preventDefault();
												selectPage(i);
											}
										}}
									>
										<span
											class="bg-muted text-muted-foreground flex size-4 items-center justify-center rounded text-[9px] font-semibold"
										>
											{i + 1}
										</span>
										<span class="flex-1 truncate">Page {i + 1}</span>
										<span class="text-muted-foreground text-[10px]">{p.blocks.length}</span>
										{#if content.pages.length > 1}
											<button
												class="text-muted-foreground hover:text-destructive rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-100"
												title="Delete page"
												onclick={(e) => {
													e.stopPropagation();
													deletePage(i);
												}}
											>
												<Trash2Icon class="size-3" />
											</button>
										{/if}
									</div>
								{/each}
							</div>
						</div>
					{:else if leftTab === 'layers'}
						<div class="flex gap-1 overflow-x-auto border-b px-2 py-1.5">
							{#each content.pages as p, i (p.uid)}
								<button
									class={[
										'shrink-0 rounded px-2 py-0.5 text-[10px] font-medium transition-colors',
										i === safeIndex
											? 'bg-primary text-primary-foreground'
											: 'bg-muted text-muted-foreground hover:text-foreground'
									].join(' ')}
									onclick={() => selectPage(i)}
								>
									{i + 1}
								</button>
							{/each}
						</div>
						<div class="p-1.5">
							{#if currentPage.blocks.length === 0}
								<p class="text-muted-foreground py-6 text-center text-xs">
									No blocks on this page.
								</p>
							{:else}
								{#each currentPage.blocks as b (b.uid)}
									{@const Icon = iconFor(b.type, b.variant)}
									<button
										class={[
											'flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[11px] transition-colors',
											b.uid === selectedBlockUid
												? 'bg-accent text-accent-foreground'
												: 'text-muted-foreground hover:bg-muted/40 hover:text-foreground'
										].join(' ')}
										onclick={() => selectBlock(b.uid)}
									>
										<EyeIcon class="size-3 shrink-0 opacity-50" />
										<Icon class="size-3.5 shrink-0" />
										<span class="flex-1 truncate">{blockLabel(b)}</span>
									</button>
								{/each}
							{/if}
						</div>
					{:else if leftTab === 'blocks'}
						<div class="p-2">
							{#each BLOCK_PALETTE as group (group.title)}
								<div class="mb-2 last:mb-0">
									<div
										class="text-muted-foreground flex items-center gap-1 px-0.5 py-1 text-[10px] font-semibold tracking-wide uppercase"
									>
										<ChevronDownIcon class="size-3" />
										{group.title}
									</div>
									<div class="grid grid-cols-2 gap-1.5">
										{#each group.items as item (item.label)}
											{@const Icon = iconFor(item.type, item.variant)}
											<button
												class="bg-card hover:border-ring hover:bg-muted/40 hover:text-foreground text-muted-foreground flex flex-col items-center justify-center gap-1 rounded border p-2 text-center text-[10px] leading-tight transition-colors"
												title={item.label}
												onclick={() => addBlock(item.type, item.variant)}
											>
												<Icon class="size-4 opacity-80" />
												<span class="line-clamp-2">{item.label}</span>
											</button>
										{/each}
									</div>
								</div>
							{/each}
						</div>
					{:else if leftTab === 'datasets'}
						<div class="p-3">
							{#if linkedDatasets.length === 0}
								<p class="text-muted-foreground py-6 text-center text-xs">
									No datasets linked to this template.
								</p>
							{:else}
								<p class="text-muted-foreground mb-2 text-[11px]">
									Insert fields as <code class="bg-muted rounded px-1 font-mono">{'{{ … }}'}</code> tokens.
								</p>
								<div class="space-y-1.5">
									{#each linkedDatasets as ds (ds.uid)}
										{@const sm = DATASET_FORMAT_META[ds.format]}
										<div class="rounded-md border p-2">
											<div class="flex items-center justify-between gap-2">
												<span class="truncate text-xs font-medium">{ds.name}</span>
												<span
													class="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-medium {sm.bgColor} {sm.textColor}"
												>
													{sm.label}
												</span>
											</div>
											<p class="text-muted-foreground mt-0.5 truncate font-mono text-[10px]">
												{ds.code}
											</p>
										</div>
									{/each}
								</div>
							{/if}
						</div>
					{/if}
				</div>
			</aside>
		{/if}

		<!-- Centre canvas — workspace transparent so the gradient shows behind the page;
			 the toolbar bar above it stays solid. -->
		<div class="flex min-w-0 flex-1 flex-col">
			<div
				class="bg-card flex h-10 shrink-0 items-center justify-between gap-1 border-b px-3 text-xs"
			>
				<div class="flex items-center gap-1">
					<Popover.Root>
						<Popover.Trigger
							class="hover:bg-muted text-muted-foreground hover:text-foreground flex items-center gap-1.5 rounded px-2 py-1 transition-colors"
						>
							<PaletteIcon class="size-3.5" />
							Theme
						</Popover.Trigger>
						<Popover.Content align="start" class="w-72">
							<p class="text-sm font-semibold">Theme</p>
							<p class="text-muted-foreground -mt-1 text-xs">Applies to all pages.</p>
							<div class="space-y-2.5">
								<div class="space-y-1">
									<Label class="text-xs">Font family</Label>
									<SelectMenu
										bind:value={themeFont}
										size="sm"
										items={['Inter', 'Georgia', 'Arial', 'Times New Roman'].map((f) => ({
											value: f,
											label: f
										}))}
									/>
								</div>
								<div class="space-y-1">
									<Label class="text-xs">Base font size (px)</Label>
									<Input type="number" min="8" bind:value={themeBase} class="h-8" />
								</div>
								<div class="grid grid-cols-2 gap-2">
									<div class="space-y-1">
										<Label class="text-xs">Brand</Label>
										<div class="flex items-center gap-2">
											<input
												type="color"
												bind:value={brandColor}
												class="border-input size-8 shrink-0 cursor-pointer rounded border bg-transparent p-0.5"
											/>
											<span class="text-muted-foreground font-mono text-[11px]">{brandColor}</span>
										</div>
									</div>
									<div class="space-y-1">
										<Label class="text-xs">Headings</Label>
										<div class="flex items-center gap-2">
											<input
												type="color"
												bind:value={headingColor}
												class="border-input size-8 shrink-0 cursor-pointer rounded border bg-transparent p-0.5"
											/>
											<span class="text-muted-foreground font-mono text-[11px]">{headingColor}</span
											>
										</div>
									</div>
								</div>
								<p class="text-muted-foreground/70 text-[11px]">
									Mock — theme tokens are not yet applied to the canvas.
								</p>
							</div>
						</Popover.Content>
					</Popover.Root>

					<Popover.Root>
						<Popover.Trigger
							class="hover:bg-muted text-muted-foreground hover:text-foreground flex items-center gap-1.5 rounded px-2 py-1 transition-colors"
						>
							<HistoryIcon class="size-3.5" />
							History
						</Popover.Trigger>
						<Popover.Content align="start" class="w-72">
							<p class="text-sm font-semibold">History</p>
							<div class="mt-1 space-y-0">
								{#each historyItems as h, i (i)}
									<div class="flex gap-2.5">
										<div class="flex flex-col items-center">
											<span class="bg-primary/60 mt-1.5 size-2 shrink-0 rounded-full"></span>
											{#if i < historyItems.length - 1}
												<span class="bg-border w-px flex-1"></span>
											{/if}
										</div>
										<div class="pb-3">
											<p class="text-xs font-medium leading-tight">{h.action}</p>
											<p class="text-muted-foreground text-[11px]">{h.who} · {h.when}</p>
										</div>
									</div>
								{/each}
							</div>
							<p class="text-muted-foreground/70 text-[11px]">Mock change history.</p>
						</Popover.Content>
					</Popover.Root>
				</div>

				<div class="flex items-center gap-1">
					<button
						class="hover:bg-muted text-muted-foreground rounded p-1.5"
						title="Zoom out"
						onclick={() => setZoom(-0.1)}
					>
						<MinusIcon class="size-3.5" />
					</button>
					<span class="w-12 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
					<button
						class="hover:bg-muted text-muted-foreground rounded p-1.5"
						title="Zoom in"
						onclick={() => setZoom(0.1)}
					>
						<PlusIcon class="size-3.5" />
					</button>
				</div>
			</div>

			<!-- Neutral grey workspace behind the print-styled page; clicking it deselects. -->
			<div class="min-h-0 flex-1 overflow-auto p-10" role="presentation" onclick={deselect}>
				<div class="relative mx-auto" style="width:{sz.w * zoom}px; height:{sz.h * zoom}px;">
					<div
						class="absolute top-0 left-0"
						style="width:{sz.w}px; height:{sz.h}px; transform:scale({zoom}); transform-origin:top left;"
					>
						<TemplatePageView
							{content}
							page={currentPage}
							selectable={!preview}
							{selectedBlockUid}
							onselectblock={selectBlock}
						/>
					</div>
				</div>
			</div>
		</div>

		<!-- Right panel -->
		{#if !preview}
			<aside class="glass-panel flex w-64 shrink-0 flex-col overflow-hidden border-l">
				<div class="bg-muted/40 flex shrink-0 border-b text-[10px]">
					{#each rightTabs as t (t.key)}
						<button
							class={[
								'relative flex-1 py-1.5 font-medium transition-colors',
								rightTab === t.key
									? 'text-foreground bg-card border-t-foreground -mb-px border-t-2'
									: 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
							].join(' ')}
							onclick={() => (rightTab = t.key)}
						>
							{t.label}
						</button>
					{/each}
				</div>

				<div class="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
					{#if rightTab === 'page'}
						<div>
							<p
								class="text-muted-foreground mb-2 text-[11px] font-semibold tracking-wide uppercase"
							>
								Layout
							</p>
							<div class="grid grid-cols-3 gap-1.5">
								{#each PAGE_SIZES as s (s)}
									<button
										class={[
											'rounded-md border py-1.5 text-xs font-medium transition-colors',
											content.size === s
												? 'border-primary bg-primary text-primary-foreground'
												: 'hover:border-ring/50 hover:bg-muted/40'
										].join(' ')}
										onclick={() => (content.size = s as PageSize)}
									>
										{s}
									</button>
								{/each}
							</div>
							<div class="mt-2 grid grid-cols-2 gap-1.5">
								<button
									class={[
										'flex items-center justify-center gap-1.5 rounded-md border py-1.5 text-xs font-medium transition-colors',
										content.orientation === 'portrait'
											? 'border-primary bg-primary text-primary-foreground'
											: 'hover:border-ring/50 hover:bg-muted/40'
									].join(' ')}
									onclick={() => (content.orientation = 'portrait')}
								>
									<RectangleVerticalIcon class="size-3.5" />
									Portrait
								</button>
								<button
									class={[
										'flex items-center justify-center gap-1.5 rounded-md border py-1.5 text-xs font-medium transition-colors',
										content.orientation === 'landscape'
											? 'border-primary bg-primary text-primary-foreground'
											: 'hover:border-ring/50 hover:bg-muted/40'
									].join(' ')}
									onclick={() => (content.orientation = 'landscape')}
								>
									<RectangleHorizontalIcon class="size-3.5" />
									Landscape
								</button>
							</div>
						</div>

						<div>
							<p
								class="text-muted-foreground mb-2 text-center text-[11px] font-semibold tracking-wide uppercase"
							>
								This page
							</p>
							<div class="flex items-center justify-between rounded-lg border px-2 py-1.5">
								<button
									class="hover:bg-muted rounded p-1 disabled:opacity-30"
									disabled={safeIndex === 0}
									onclick={() => selectPage(safeIndex - 1)}
								>
									<ChevronLeftIcon class="size-4" />
								</button>
								<span class="text-xs font-medium">
									Page {safeIndex + 1} of {content.pages.length}
								</span>
								<button
									class="hover:bg-muted rounded p-1 disabled:opacity-30"
									disabled={safeIndex >= content.pages.length - 1}
									onclick={() => selectPage(safeIndex + 1)}
								>
									<ChevronRightIcon class="size-4" />
								</button>
							</div>
						</div>

						<div>
							<p
								class="text-muted-foreground mb-2 text-[11px] font-semibold tracking-wide uppercase"
							>
								Margins (mm)
							</p>
							<div
								class="border-warning/40 bg-warning/5 relative mx-auto h-28 w-full max-w-[210px] rounded-lg border"
							>
								<div
									class="border-warning/50 text-warning/60 absolute inset-7 flex items-center justify-center rounded border border-dashed text-[10px]"
								>
									page
								</div>
								<input
									type="number"
									min="0"
									aria-label="Top margin (mm)"
									bind:value={content.margins.top}
									class="border-warning/60 bg-background text-warning absolute top-1 left-1/2 w-11 -translate-x-1/2 rounded border py-0.5 text-center text-xs"
								/>
								<input
									type="number"
									min="0"
									aria-label="Bottom margin (mm)"
									bind:value={content.margins.bottom}
									class="border-warning/60 bg-background text-warning absolute bottom-1 left-1/2 w-11 -translate-x-1/2 rounded border py-0.5 text-center text-xs"
								/>
								<input
									type="number"
									min="0"
									aria-label="Left margin (mm)"
									bind:value={content.margins.left}
									class="border-warning/60 bg-background text-warning absolute top-1/2 left-1 w-11 -translate-y-1/2 rounded border py-0.5 text-center text-xs"
								/>
								<input
									type="number"
									min="0"
									aria-label="Right margin (mm)"
									bind:value={content.margins.right}
									class="border-warning/60 bg-background text-warning absolute top-1/2 right-1 w-11 -translate-y-1/2 rounded border py-0.5 text-center text-xs"
								/>
							</div>
						</div>

						<div>
							<p
								class="text-muted-foreground mb-2 text-[11px] font-semibold tracking-wide uppercase"
							>
								Background
							</p>
							<div class="grid grid-cols-3 gap-1.5">
								<button
									class={[
										'rounded-md border py-1.5 text-xs font-medium transition-colors',
										content.background === 'none'
											? 'border-primary bg-primary text-primary-foreground'
											: 'hover:border-ring/50 hover:bg-muted/40'
									].join(' ')}
									onclick={() => (content.background = 'none')}
								>
									None
								</button>
								<button
									class={[
										'rounded-md border py-1.5 text-xs font-medium transition-colors',
										content.background === 'color'
											? 'border-primary bg-primary text-primary-foreground'
											: 'hover:border-ring/50 hover:bg-muted/40'
									].join(' ')}
									onclick={() => (content.background = 'color')}
								>
									Colour
								</button>
								<button
									class="text-muted-foreground/50 cursor-not-allowed rounded-md border py-1.5 text-xs font-medium"
									disabled
								>
									Image
								</button>
							</div>
						</div>

						<div>
							<p
								class="text-muted-foreground mb-2 text-[11px] font-semibold tracking-wide uppercase"
							>
								Border
							</p>
							<div class="grid grid-cols-4 gap-1.5">
								{#each borderStyles as b (b.key)}
									<button
										class={[
											'rounded-md border py-1.5 text-[11px] font-medium transition-colors',
											content.border === b.key
												? 'border-primary bg-primary text-primary-foreground'
												: 'hover:border-ring/50 hover:bg-muted/40'
										].join(' ')}
										onclick={() => (content.border = b.key)}
									>
										{b.label}
									</button>
								{/each}
							</div>
						</div>
					{:else if rightTab === 'component'}
						{#if !selectedBlock}
							<div class="flex flex-col items-center gap-2 py-10 text-center">
								<SlidersIcon class="text-muted-foreground/40 size-7" />
								<p class="text-muted-foreground text-xs">
									Select a block on the canvas to edit its properties.
								</p>
							</div>
						{:else}
							<div class="flex items-center justify-between">
								<span
									class="bg-muted text-muted-foreground rounded px-2 py-0.5 text-[11px] font-medium"
								>
									{BLOCK_LABELS[selectedBlock.type]}
								</span>
								<div class="flex items-center gap-0.5">
									<button
										class="hover:bg-muted text-muted-foreground rounded p-1"
										title="Move up"
										onclick={() => moveBlock(selectedBlock.uid, -1)}
									>
										<ChevronUpIcon class="size-4" />
									</button>
									<button
										class="hover:bg-muted text-muted-foreground rounded p-1"
										title="Move down"
										onclick={() => moveBlock(selectedBlock.uid, 1)}
									>
										<ChevronDownIcon class="size-4" />
									</button>
									<button
										class="text-muted-foreground hover:text-destructive rounded p-1"
										title="Delete block"
										onclick={() => deleteBlock(selectedBlock.uid)}
									>
										<Trash2Icon class="size-4" />
									</button>
								</div>
							</div>

							<!-- Content controls per block type -->
							{#if selectedBlock.type === 'heading' || selectedBlock.type === 'paragraph'}
								<div class="space-y-1.5">
									<Label for="blk-text">Text</Label>
									<Textarea
										id="blk-text"
										rows={selectedBlock.type === 'heading' ? 2 : 5}
										bind:value={selectedBlock.text}
									/>
									<p class="text-muted-foreground text-[11px]">
										Use <code class="bg-muted rounded px-1 font-mono">{'{{ field }}'}</code> for merge
										fields.
									</p>
								</div>
							{:else if selectedBlock.type === 'bulletList' || selectedBlock.type === 'numberedList'}
								<div class="space-y-1.5">
									<Label for="blk-items">List items (one per line)</Label>
									<Textarea id="blk-items" rows={5} bind:value={selectedBlock.text} />
								</div>
							{:else if selectedBlock.type === 'labelValue'}
								<div class="space-y-1.5">
									<Label for="blk-label">Label</Label>
									<Input id="blk-label" bind:value={selectedBlock.label} />
								</div>
								<div class="space-y-1.5">
									<Label for="blk-value">Value</Label>
									<Input
										id="blk-value"
										bind:value={selectedBlock.text}
										placeholder={'{{ field }}'}
									/>
								</div>
							{:else if selectedBlock.type === 'image'}
								<div class="space-y-1.5">
									<Label for="blk-word">Wordmark</Label>
									<Input id="blk-word" bind:value={selectedBlock.label} placeholder="Firm name" />
									<p class="text-muted-foreground text-[11px]">Shown in place of a logo image.</p>
								</div>
							{:else if selectedBlock.type === 'noteBox' || selectedBlock.type === 'highlightBox'}
								<div class="space-y-1.5">
									<Label for="blk-title">Title (optional)</Label>
									<Input id="blk-title" bind:value={selectedBlock.label} />
								</div>
								<div class="space-y-1.5">
									<Label for="blk-body">Body</Label>
									<Textarea id="blk-body" rows={3} bind:value={selectedBlock.text} />
								</div>
							{:else if selectedBlock.type === 'spacer'}
								<div class="space-y-1.5">
									<Label for="blk-h">Height (px)</Label>
									<Input id="blk-h" type="number" min="0" bind:value={selectedBlock.height} />
								</div>
							{:else if selectedBlock.type === 'columns'}
								<div class="space-y-1.5">
									<Label for="blk-cols">Layout</Label>
									<SelectMenu
										id="blk-cols"
										bind:value={selectedBlock.variant}
										items={[
											{ value: 'full', label: 'Full width' },
											{ value: 'two', label: 'Two columns' }
										]}
									/>
								</div>
							{:else if selectedBlock.type === 'chart'}
								<div class="space-y-1.5">
									<Label for="blk-ctitle">Title</Label>
									<Input id="blk-ctitle" bind:value={selectedBlock.label} />
								</div>
								<div class="space-y-1.5">
									<Label for="blk-ctype">Chart type</Label>
									<SelectMenu
										id="blk-ctype"
										bind:value={selectedBlock.variant}
										items={[
											{ value: 'bar', label: 'Bar' },
											{ value: 'line', label: 'Line' },
											{ value: 'pie', label: 'Pie' }
										]}
									/>
								</div>
							{:else if selectedBlock.type === 'table'}
								<div class="space-y-1.5">
									<Label for="blk-ttitle">Title</Label>
									<Input id="blk-ttitle" bind:value={selectedBlock.label} />
								</div>
							{:else if selectedBlock.type === 'conditional'}
								<div class="space-y-1.5">
									<Label for="blk-cond">Condition</Label>
									<Input id="blk-cond" class="font-mono text-sm" bind:value={selectedBlock.text} />
									<p class="text-muted-foreground text-[11px]">Block renders only if truthy.</p>
								</div>
							{:else if selectedBlock.type === 'repeat'}
								<div class="space-y-1.5">
									<Label for="blk-rep">Collection</Label>
									<Input id="blk-rep" class="font-mono text-sm" bind:value={selectedBlock.text} />
									<p class="text-muted-foreground text-[11px]">Repeats for each item.</p>
								</div>
							{:else if selectedBlock.type === 'divider'}
								<p class="text-muted-foreground text-xs">A horizontal rule. No further options.</p>
							{:else if selectedBlock.type === 'pageBreak'}
								<p class="text-muted-foreground text-xs">Forces a new page when rendered.</p>
							{/if}

							<!-- Style accordion -->
							<div class="border-t pt-1">
								{#each stylePre as sec (sec.key)}
									{@render mockSection(sec)}
								{/each}

								<div class="border-b">
									<button
										class="text-foreground flex w-full items-center justify-between py-2.5 text-xs font-semibold"
										onclick={() => toggleSection('typography')}
									>
										Typography
										<ChevronDownIcon
											class="size-3.5 transition-transform {openSections.typography
												? ''
												: '-rotate-90'}"
										/>
									</button>
									{#if openSections.typography}
										<div class="space-y-2.5 pb-3">
											<div class="grid grid-cols-2 gap-2">
												<div class="space-y-1">
													<span class="text-muted-foreground text-[10px]">Size</span>
													<Input
														type="number"
														min="0"
														placeholder="inherit"
														bind:value={selectedBlock.fontSize}
														class="h-7 text-xs"
													/>
												</div>
												<div class="space-y-1">
													<span class="text-muted-foreground text-[10px]">Line height</span>
													<Input
														type="number"
														min="0"
														step="0.1"
														placeholder="inherit"
														bind:value={selectedBlock.lineHeight}
														class="h-7 text-xs"
													/>
												</div>
											</div>
											<div class="flex items-center gap-1">
												<button
													class={[
														'flex size-7 items-center justify-center rounded-md border transition-colors',
														selectedBlock.bold
															? 'border-primary bg-primary text-primary-foreground'
															: 'hover:border-ring/50 hover:bg-muted/40'
													].join(' ')}
													title="Bold"
													onclick={() => (selectedBlock.bold = !selectedBlock.bold)}
												>
													<BoldIcon class="size-3.5" />
												</button>
												<button
													class={[
														'flex size-7 items-center justify-center rounded-md border transition-colors',
														selectedBlock.italic
															? 'border-primary bg-primary text-primary-foreground'
															: 'hover:border-ring/50 hover:bg-muted/40'
													].join(' ')}
													title="Italic"
													onclick={() => (selectedBlock.italic = !selectedBlock.italic)}
												>
													<ItalicIcon class="size-3.5" />
												</button>
												<button
													class={[
														'flex size-7 items-center justify-center rounded-md border transition-colors',
														selectedBlock.underline
															? 'border-primary bg-primary text-primary-foreground'
															: 'hover:border-ring/50 hover:bg-muted/40'
													].join(' ')}
													title="Underline"
													onclick={() => (selectedBlock.underline = !selectedBlock.underline)}
												>
													<UnderlineIcon class="size-3.5" />
												</button>
												<span class="bg-border mx-0.5 h-5 w-px"></span>
												{#each [{ a: 'left', Icon: AlignLeftIcon }, { a: 'center', Icon: AlignCenterIcon }, { a: 'right', Icon: AlignRightIcon }, { a: 'justify', Icon: AlignJustifyIcon }] as opt (opt.a)}
													{@const Icon = opt.Icon}
													<button
														class={[
															'flex size-7 items-center justify-center rounded-md border transition-colors',
															(selectedBlock.align ?? 'left') === opt.a
																? 'border-primary bg-primary text-primary-foreground'
																: 'hover:border-ring/50 hover:bg-muted/40'
														].join(' ')}
														title={opt.a}
														onclick={() => (selectedBlock.align = opt.a as BlockAlign)}
													>
														<Icon class="size-3.5" />
													</button>
												{/each}
											</div>
											<div class="flex items-center gap-2">
												<input
													type="color"
													aria-label="Text colour"
													value={selectedBlock.color ?? '#0f172a'}
													oninput={(e) => (selectedBlock.color = e.currentTarget.value)}
													class="border-input size-8 shrink-0 cursor-pointer rounded border bg-transparent p-0.5"
												/>
												<Input
													placeholder="inherit"
													bind:value={selectedBlock.color}
													class="h-7 font-mono text-xs"
												/>
											</div>
										</div>
									{/if}
								</div>

								{#each stylePost as sec (sec.key)}
									{@render mockSection(sec)}
								{/each}
							</div>
						{/if}
					{:else if rightTab === 'header'}
						<div class="flex items-start gap-2 rounded-lg border border-dashed p-3">
							<PanelTopIcon class="text-muted-foreground mt-0.5 size-4 shrink-0" />
							<p class="text-muted-foreground text-xs">
								Headers and footers repeat on every page. These settings aren't editable yet.
							</p>
						</div>
						<label class="flex items-center justify-between text-sm">
							<span>Show header</span>
							<Switch />
						</label>
						<label class="flex items-center justify-between text-sm">
							<span>Show footer</span>
							<Switch checked />
						</label>
						<label class="flex items-center justify-between text-sm">
							<span>Page numbers</span>
							<Switch checked />
						</label>
					{:else if rightTab === 'details'}
						<dl class="space-y-3 text-sm">
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground">Name</dt>
								<dd class="truncate text-right font-medium">{template.name}</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground">Code</dt>
								<dd>
									<code class="bg-muted rounded px-1.5 py-0.5 font-mono text-xs"
										>{template.code}</code
									>
								</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground">Document type</dt>
								<dd>
									<span
										class="rounded-full px-2 py-0.5 text-xs font-medium {dtm.bgColor} {dtm.textColor}"
									>
										{dtm.label}
									</span>
								</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground">Renderer</dt>
								<dd class="font-medium">WeasyPrint</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground">Pages</dt>
								<dd class="font-medium">{content.pages.length}</dd>
							</div>
						</dl>
						{#if template.description}
							<div class="border-t pt-3">
								<p
									class="text-muted-foreground mb-1 text-[11px] font-semibold tracking-wide uppercase"
								>
									Description
								</p>
								<p class="text-muted-foreground text-xs leading-relaxed">{template.description}</p>
							</div>
						{/if}
					{/if}
				</div>
			</aside>
		{/if}
	</div>
</div>
