<script lang="ts">
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import { cn } from '../../../utils.js';
	import PdfViewer from '../pdf-viewer/pdf-viewer.svelte';

	let {
		src,
		contentType,
		name = 'document',
		expanded = false,
		onexpand,
		searchable = true,
		zoomable = true,
		class: className
	}: {
		/** URL of the document bytes (a proxied stream or signed URL). Omit to show a placeholder. */
		src?: string;
		/** MIME type; decides inline rendering (image vs embedded PDF). */
		contentType?: string;
		name?: string;
		/** Whether the host has given the viewer the whole page. */
		expanded?: boolean;
		/** Absent ⇒ no expand affordance. Forwarded to the pdf.js renderer alone: it is the only
		 *  branch here with its own floating chrome to hang the toggle off, and the host's layout
		 *  state belongs to the host either way (which sidebar to hide is not the viewer's business). */
		onexpand?: (next: boolean) => void;
		searchable?: boolean;
		zoomable?: boolean;
		class?: string;
	} = $props();

	const isImage = $derived(!!contentType && contentType.startsWith('image/'));
	const isPdf = $derived(contentType === 'application/pdf');
	const isHtml = $derived(contentType === 'text/html');

	// A PDF is drawn by pdf.js (PdfViewer) rather than handed to the browser's built-in plugin in an
	// <iframe>. The plugin brings its own toolbar, grey surround and scrollbar — three pieces of
	// chrome we don't control, that ignore the theme, and that differ per browser — and `#toolbar=0`
	// only ever suppressed the first of them, and only where the browser chose to honour it.
	//
	// The trade is text: a rendered page has no selectable text and no in-page browser find. That is
	// a real loss for a long report, so it is written down rather than assumed away — pdf.js can
	// render a text layer if this turns out to be missed.

	// Track a load failure so a broken stream falls back to a clear message rather than a blank box.
	// Reset whenever the source changes (navigating between documents reuses this component).
	let failed = $state(false);
	$effect(() => {
		void src;
		failed = false;
	});
</script>

<div
	data-slot="document-viewer"
	class={cn('bg-muted/30 flex h-full w-full items-center justify-center overflow-auto', className)}
>
	{#if src && isImage && !failed}
		<img
			{src}
			alt={name}
			onerror={() => (failed = true)}
			class="max-h-full max-w-full object-contain"
		/>
	{:else if src && isPdf && !failed}
		<PdfViewer {src} {expanded} {onexpand} {searchable} {zoomable} class="h-full w-full" />
	{:else if src && isHtml && !failed}
		<!-- HTML still goes to an iframe: it is a document to be DISPLAYED as itself, and the
		     sandbox is the point. Key on src so switching documents remounts a fresh frame (a reused
		     iframe can keep the previous document, or fail to reload the new one). -->
		{#key src}
			<iframe {src} title={name} onerror={() => (failed = true)} class="h-full w-full border-0"
			></iframe>
		{/key}
	{:else}
		<div class="text-muted-foreground flex flex-col items-center gap-2 p-8 text-center text-sm">
			<FileTextIcon class="size-10 opacity-40" />
			<span>
				{#if !src}
					No preview available
				{:else if failed}
					Preview unavailable — download to view.
				{:else}
					This file type can’t be previewed — download to view.
				{/if}
			</span>
		</div>
	{/if}
</div>
