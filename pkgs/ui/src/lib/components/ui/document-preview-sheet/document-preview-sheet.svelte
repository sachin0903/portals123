<script lang="ts">
	import { cn } from '../../../utils.js';
	import * as Sheet from '../sheet/index.js';
	import { Button } from '../button/index.js';
	import { DocumentViewer } from '../document-viewer/index.js';
	import XIcon from '@lucide/svelte/icons/x';
	import DownloadIcon from '@lucide/svelte/icons/download';

	// A right-side slide-over that renders a document inline (PDF/image via DocumentViewer, a
	// "download to view" fallback otherwise), with a close control + optional download in the header.
	// Shared by the client portal's documents list and the platform client-documents tab — the caller
	// keeps its own `previewDoc` state and passes derived values, closing via `onOpenChange`.
	let {
		open = false,
		name = 'document',
		subtitle = undefined,
		src = undefined,
		contentType = undefined,
		downloadHref = undefined,
		downloadName = undefined,
		onOpenChange = undefined,
		class: className = undefined
	}: {
		open?: boolean;
		name?: string;
		/** Secondary line under the title (e.g. "Statement · 12 Jan 2026"). */
		subtitle?: string;
		/** Content URL streamed into the viewer; omit to show the viewer's placeholder. */
		src?: string;
		/** MIME type — decides inline rendering (image vs embedded PDF). */
		contentType?: string;
		/** When set, a Download button appears in the header linking here. */
		downloadHref?: string;
		downloadName?: string;
		onOpenChange?: (open: boolean) => void;
		class?: string;
	} = $props();
</script>

<Sheet.Root {open} {onOpenChange}>
	<Sheet.Content
		side="right"
		showCloseButton={false}
		class={cn(
			'flex flex-col gap-0 p-0 transition duration-300 ease-out data-[ending-style]:translate-x-full data-[starting-style]:translate-x-full',
			className
		)}
	>
		<div class="flex items-center gap-3 border-b px-3 py-2.5">
			<Button
				variant="ghost"
				size="icon"
				onclick={() => onOpenChange?.(false)}
				aria-label="Close preview"
				class="text-muted-foreground shrink-0"
			>
				<XIcon class="size-5" />
			</Button>
			<div class="min-w-0 flex-1">
				<Sheet.Title class="truncate text-base font-semibold">{name}</Sheet.Title>
				{#if subtitle}
					<p class="text-muted-foreground text-xs">{subtitle}</p>
				{/if}
			</div>
			{#if downloadHref}
				<Button
					variant="outline"
					size="sm"
					class="shrink-0"
					href={downloadHref}
					download={downloadName}
				>
					<DownloadIcon class="size-4" /> Download
				</Button>
			{/if}
		</div>
		<div class="min-h-0 flex-1">
			<DocumentViewer {src} {contentType} {name} class="h-full w-full" />
		</div>
	</Sheet.Content>
</Sheet.Root>
