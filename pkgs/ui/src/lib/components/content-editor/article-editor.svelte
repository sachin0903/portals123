<script lang="ts">
	import { onMount } from 'svelte';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import Heading2Icon from '@lucide/svelte/icons/heading-2';
	import ImageIcon from '@lucide/svelte/icons/image';
	import ListIcon from '@lucide/svelte/icons/list';
	import MessageSquareQuoteIcon from '@lucide/svelte/icons/message-square-quote';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import MonitorIcon from '@lucide/svelte/icons/monitor';
	import PanelTopIcon from '@lucide/svelte/icons/panel-top';
	import Redo2Icon from '@lucide/svelte/icons/redo-2';
	import SmartphoneIcon from '@lucide/svelte/icons/smartphone';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import TabletIcon from '@lucide/svelte/icons/tablet';
	import TextIcon from '@lucide/svelte/icons/text';
	import Undo2Icon from '@lucide/svelte/icons/undo-2';
	import { Button } from '../ui/button/index.js';
	import ConfirmDialog from '../ui/confirm-dialog/confirm-dialog.svelte';
	import { Skeleton } from '../ui/skeleton/index.js';
	import {
		CANVAS_HEIGHT,
		createArticleEditor,
		type ArticleBlock,
		type ArticleDevice
	} from './create.js';
	import { smartImportWord } from './word-import.js';

	let {
		html = '',
		onchange,
		onUpload
	}: {
		html?: string;
		// `css` is deliberately NOT a prop. The article stylesheet is the design system's, not the
		// item's (see `create.ts`), so the editor reports it rather than being handed it.
		onchange?: (next: { html: string; css: string }) => void;
		onUpload?: (file: File) => Promise<string>;
	} = $props();

	let host = $state<HTMLDivElement | null>(null);
	let ready = $state(false);
	let editor = $state<Awaited<ReturnType<typeof createArticleEditor>> | null>(null);
	let documentInput = $state<HTMLInputElement | null>(null);
	let imageInput = $state<HTMLInputElement | null>(null);
	let importOpen = $state(false);
	let pendingDocument = $state<File | null>(null);
	let importing = $state(false);
	let failure = $state('');
	let status = $state('Click the page to start writing.');
	let device = $state<ArticleDevice>('desktop');

	onMount(() => {
		if (!host) return;
		let cancelled = false;
		void createArticleEditor(host, html, onUpload)
			.then((ed) => {
				if (cancelled) {
					ed.destroy();
					return;
				}
				editor = ed;
				ready = true;
				ed.onChange(() => onchange?.({ html: ed.getHtml(), css: ed.getCss() }));
				// A new item stores an empty schema, but the canvas shows a real starter article. Hand
				// that starter back immediately so Publish cannot snapshot an empty body that merely
				// looked populated in the editor.
				if (!html.trim()) onchange?.({ html: ed.getHtml(), css: ed.getCss() });
			})
			// A rejected init must NOT vanish into an unhandled promise. Without this the canvas is a
			// silent grey box — the skeleton, forever — with the failure visible only to whoever
			// thinks to open a console, which is how a broken canvas reads as a broken FEATURE.
			.catch((error: unknown) => {
				failure = error instanceof Error ? error.message : 'The editor could not be loaded.';
				console.error('[article-editor] the canvas failed to load', error);
			});
		return () => {
			cancelled = true;
			editor?.destroy();
		};
	});

	function add(block: ArticleBlock): void {
		editor?.addBlock(block);
		status = 'Block added. Edit it directly on the page.';
	}

	function selectDevice(next: ArticleDevice): void {
		device = next;
		editor?.setDevice(next);
	}

	function chooseDocument(event: Event): void {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0] ?? null;
		input.value = '';
		if (!file) return;
		pendingDocument = file;
		importOpen = true;
	}

	async function importDocument(): Promise<void> {
		if (!pendingDocument || !editor) return;
		importing = true;
		status = 'Reading the document and rebuilding its structure…';
		try {
			const imported = await smartImportWord(pendingDocument, onUpload);
			editor.setContent(imported.html);
			onchange?.({ html: editor.getHtml(), css: editor.getCss() });
			status =
				imported.warnings.length > 0
					? `Imported with ${imported.warnings.length} formatting note${imported.warnings.length === 1 ? '' : 's'}. Review before publishing.`
					: 'Word content imported and restyled. Review before publishing.';
		} catch (error) {
			status = error instanceof Error ? error.message : 'The Word document could not be imported.';
		} finally {
			importing = false;
			pendingDocument = null;
		}
	}

	async function chooseImage(event: Event): Promise<void> {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0] ?? null;
		input.value = '';
		if (!file || !editor || !onUpload) return;
		status = 'Uploading image…';
		try {
			const url = await onUpload(file);
			editor.addImage(url, file.name.replace(/\.[^.]+$/, ''));
			status = 'Image added.';
		} catch {
			status = 'The image could not be uploaded.';
		}
	}
</script>

<div class="bg-card overflow-hidden rounded-xl border shadow-sm">
	<div class="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
		<div class="flex items-center gap-3">
			<div class="bg-primary/10 text-primary flex size-9 items-center justify-center rounded-xl">
				<SparklesIcon class="size-4" />
			</div>
			<div>
				<p class="text-sm font-semibold">Article canvas</p>
				<p class="text-muted-foreground text-xs">Clean, responsive portal typography</p>
			</div>
		</div>
		<div class="flex items-center gap-1">
			<Button
				type="button"
				variant="outline"
				size="sm"
				disabled={!ready || importing}
				onclick={() => documentInput?.click()}
			>
				<FileTextIcon class="size-4" />
				{importing ? 'Importing…' : 'Smart import Word'}
			</Button>
			<span class="bg-border mx-1 hidden h-5 w-px sm:block"></span>
			<Button
				type="button"
				variant="ghost"
				size="icon-sm"
				aria-label="Undo"
				disabled={!ready}
				onclick={() => editor?.undo()}
			>
				<Undo2Icon class="size-4" />
			</Button>
			<Button
				type="button"
				variant="ghost"
				size="icon-sm"
				aria-label="Redo"
				disabled={!ready}
				onclick={() => editor?.redo()}
			>
				<Redo2Icon class="size-4" />
			</Button>
		</div>
	</div>

	<div class="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/25 px-3 py-2">
		<div class="flex flex-wrap items-center gap-1" aria-label="Insert content">
			<Button
				type="button"
				variant="ghost"
				size="sm"
				disabled={!ready}
				onclick={() => add('heading')}
			>
				<Heading2Icon class="size-4" /> Heading
			</Button>
			<Button type="button" variant="ghost" size="sm" disabled={!ready} onclick={() => add('text')}>
				<TextIcon class="size-4" /> Text
			</Button>
			<Button type="button" variant="ghost" size="sm" disabled={!ready} onclick={() => add('list')}>
				<ListIcon class="size-4" /> List
			</Button>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				disabled={!ready}
				onclick={() => add('quote')}
			>
				<MessageSquareQuoteIcon class="size-4" /> Quote
			</Button>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				disabled={!ready}
				onclick={() => add('callout')}
			>
				<PanelTopIcon class="size-4" /> Callout
			</Button>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				disabled={!ready}
				onclick={() => add('divider')}
			>
				<MinusIcon class="size-4" /> Divider
			</Button>
			{#if onUpload}
				<Button
					type="button"
					variant="ghost"
					size="sm"
					disabled={!ready}
					onclick={() => imageInput?.click()}
				>
					<ImageIcon class="size-4" /> Image
				</Button>
			{/if}
		</div>
		<div
			class="bg-background hidden items-center rounded-lg border p-0.5 md:flex"
			aria-label="Preview width"
		>
			<Button
				type="button"
				variant={device === 'desktop' ? 'secondary' : 'ghost'}
				size="icon-sm"
				aria-label="Desktop preview"
				onclick={() => selectDevice('desktop')}
			>
				<MonitorIcon class="size-4" />
			</Button>
			<Button
				type="button"
				variant={device === 'tablet' ? 'secondary' : 'ghost'}
				size="icon-sm"
				aria-label="Tablet preview"
				onclick={() => selectDevice('tablet')}
			>
				<TabletIcon class="size-4" />
			</Button>
			<Button
				type="button"
				variant={device === 'mobile' ? 'secondary' : 'ghost'}
				size="icon-sm"
				aria-label="Mobile preview"
				onclick={() => selectDevice('mobile')}
			>
				<SmartphoneIcon class="size-4" />
			</Button>
		</div>
	</div>

	<div class="bg-muted/60 relative p-4 sm:p-7">
		{#if failure}
			<p class="text-destructive absolute inset-7 text-sm">
				{failure} Reload the page to try again.
			</p>
		{:else if !ready}
			<Skeleton class="absolute inset-7 rounded-xl" />
		{/if}
		<!-- The height comes from CANVAS_HEIGHT, not a Tailwind class: GrapesJS overwrites this
		     element's inline height with the one it was configured with, so the two must be the same
		     number and there is only one of them. -->
		<div
			bind:this={host}
			class="article-gjs overflow-hidden rounded-xl"
			style:height={CANVAS_HEIGHT}
		></div>
	</div>

	<div
		class="text-muted-foreground flex items-center gap-2 border-t px-4 py-2.5 text-xs"
		aria-live="polite"
	>
		<span class="bg-success size-1.5 rounded-full"></span>
		{status}
	</div>
</div>

<input
	bind:this={documentInput}
	type="file"
	accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
	class="sr-only"
	onchange={chooseDocument}
/>
<input
	bind:this={imageInput}
	type="file"
	accept="image/png,image/jpeg,image/gif,image/webp"
	class="sr-only"
	onchange={chooseImage}
/>

<ConfirmDialog
	bind:open={importOpen}
	title="Replace this draft with the Word document?"
	description="Smart import keeps headings, lists, links, tables and images, then replaces Word formatting with the portal’s editorial style. The current canvas will be replaced, but nothing is saved until you choose Save."
	confirmLabel="Import and restyle"
	pendingLabel="Importing…"
	onconfirm={importDocument}
/>
