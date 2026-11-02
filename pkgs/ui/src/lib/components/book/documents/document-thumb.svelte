<script lang="ts">
	// A grid card's preview. Near the viewport, `src?preview=1` (records no view) is fetched once and
	// drawn by content-type: PDF page 1, an image, or sandboxed HTML; anything else shows the icon.
	import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
	import type { PDFDocumentLoadingTask, RenderTask } from 'pdfjs-dist';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import { cn } from '../../../utils.js';
	import EmailPreviewFrame from '../../email/email-preview-frame.svelte';
	import { Skeleton } from '../../ui/skeleton/index.js';
	import type { DocumentRow } from './display.js';
	import { FORMAT_META } from './format-meta.js';

	let {
		src,
		format,
		status = 'ready'
	}: {
		src: string;
		/** Picks the icon. Only a format in `PREVIEWABLE` is fetched. */
		format: DocumentRow['documentType'];
		/** `rendering` (queued/generating) and `failed` have no bytes yet: nothing is fetched. */
		status?: 'ready' | 'rendering' | 'failed';
	} = $props();

	/** The HTML frame's internal width (px), scaled down to the sheet. */
	const HTML_WIDTH = 640;
	const PREVIEWABLE = new Set<DocumentRow['documentType']>(['pdf', 'jpg', 'png', 'html']);
	const SHEET = 'absolute inset-x-3 top-3 rounded-t-sm shadow-sm';
	const LINES = ['w-1/2', 'w-full', 'w-11/12', 'w-full', 'w-4/5', 'w-full', 'w-2/3'];

	let frame = $state<HTMLDivElement>();
	let canvas = $state<HTMLCanvasElement>();
	let sheetWidth = $state(0);
	let near = $state(false);
	let phase = $state<'loading' | 'pdf' | 'image' | 'html' | 'icon' | 'error'>('loading');
	let ready = $state(false);
	let pdf = $state.raw<Uint8Array>();
	let imageUrl = $state<string>();
	let html = $state('');

	const meta = $derived(FORMAT_META[format]);

	$effect(() => {
		if (!frame || near) return;
		const observer = new IntersectionObserver(
			(entries) => {
				if (!entries.some((e) => e.isIntersecting)) return;
				near = true;
				observer.disconnect();
			},
			{ rootMargin: '200px' }
		);
		observer.observe(frame);
		return () => observer.disconnect();
	});

	$effect(() => {
		phase = 'loading';
		ready = false;
		pdf = undefined;
		imageUrl = undefined;
		if (!near || status !== 'ready') return;
		if (!PREVIEWABLE.has(format)) return void (phase = 'icon');
		const url = `${src}${src.includes('?') ? '&' : '?'}preview=1`;
		const ctrl = new AbortController();
		let objectUrl: string | undefined;
		void (async () => {
			try {
				const res = await fetch(url, { signal: ctrl.signal });
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				if (res.status === 204) return void (phase = 'icon');
				const type =
					(res.headers.get('content-type') ?? '').split(';')[0]?.trim().toLowerCase() ?? '';
				if (type === 'application/pdf') {
					const bytes = new Uint8Array(await res.arrayBuffer());
					if (ctrl.signal.aborted) return;
					pdf = bytes;
					phase = 'pdf';
				} else if (type.startsWith('image/')) {
					const blob = await res.blob();
					if (ctrl.signal.aborted) return;
					objectUrl = URL.createObjectURL(blob);
					imageUrl = objectUrl;
					phase = 'image';
				} else if (type === 'text/html') {
					const body = await res.text();
					if (ctrl.signal.aborted) return;
					html = body;
					phase = 'html';
					ready = true;
				} else {
					await res.body?.cancel();
					if (!ctrl.signal.aborted) phase = 'icon';
				}
			} catch {
				if (!ctrl.signal.aborted) phase = 'error';
			}
		})();
		return () => {
			ctrl.abort();
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	});

	$effect(() => {
		const target = canvas;
		const data = pdf;
		if (!target || !data) return;
		let cancelled = false;
		let task: PDFDocumentLoadingTask | undefined;
		let render: RenderTask | undefined;
		void (async () => {
			try {
				const pdfjs = await import('pdfjs-dist');
				if (cancelled) return;
				pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
				task = pdfjs.getDocument({ data });
				const page = await (await task.promise).getPage(1);
				if (cancelled) return;
				// Drawn at card width × devicePixelRatio, shown at card width: crisp on any screen.
				const width = target.clientWidth || 240;
				const dpr = Math.min(window.devicePixelRatio || 1, 3);
				const viewport = page.getViewport({
					scale: (width / page.getViewport({ scale: 1 }).width) * dpr
				});
				const ctx = target.getContext('2d');
				if (!ctx) throw new Error('No 2D context');
				target.width = Math.floor(viewport.width);
				target.height = Math.floor(viewport.height);
				render = page.render({ canvas: target, canvasContext: ctx, viewport });
				await render.promise;
				render = undefined;
				if (!cancelled) ready = true;
			} catch {
				if (!cancelled) phase = 'error';
			} finally {
				// The bitmap stays on the canvas; the document and its worker are no longer needed.
				void task?.destroy();
				task = undefined;
			}
		})();
		return () => {
			cancelled = true;
			render?.cancel();
			void task?.destroy();
		};
	});
</script>

<div
	bind:this={frame}
	data-slot="document-thumb"
	data-status={status}
	data-phase={phase}
	class="bg-muted/60 relative aspect-[4/3] overflow-hidden rounded-lg"
>
	{#if status === 'rendering'}
		<!-- Paper is white in either theme, as in pdf-viewer's sheets. -->
		<div class="{SHEET} bottom-0 opacity-70" style:background="white">
			<div class="flex h-full animate-pulse items-center justify-center">
				<span class="text-muted-foreground text-xs">Rendering…</span>
			</div>
		</div>
	{:else if status === 'failed'}
		<div class="absolute inset-0 flex flex-col items-center justify-center gap-1.5">
			<TriangleAlertIcon class="text-destructive size-6" aria-hidden="true" />
			<span class="text-destructive text-xs">Couldn’t render</span>
		</div>
	{:else if phase === 'icon' || phase === 'error'}
		{@const Icon = meta.icon}
		<div
			class="text-muted-foreground absolute inset-0 flex flex-col items-center justify-center gap-1.5"
		>
			{#if phase === 'icon'}
				<Icon class="size-8 {meta.cls}" aria-hidden="true" />
			{:else}
				<Icon class="size-6 opacity-60" aria-hidden="true" />
				<span class="text-xs">Preview unavailable</span>
			{/if}
		</div>
	{:else}
		{#if !ready}
			<div
				data-slot="document-thumb-skeleton"
				class="{SHEET} bottom-0 flex flex-col gap-2 p-4"
				style:background="white"
				aria-hidden="true"
			>
				{#each LINES as width, i (i)}
					<Skeleton class="{width} {i === 0 ? 'mb-1 h-2.5' : 'h-1.5'} rounded-sm" />
				{/each}
			</div>
		{/if}
		{#if phase === 'image' && imageUrl}
			<img
				src={imageUrl}
				alt=""
				class={cn(
					'absolute inset-0 size-full object-cover object-top transition-opacity duration-300',
					ready ? 'opacity-100' : 'opacity-0'
				)}
				onload={() => (ready = true)}
				onerror={() => (phase = 'error')}
			/>
		{:else if phase === 'pdf'}
			<!-- The top of page 1, cropped by the frame's aspect. -->
			<canvas
				bind:this={canvas}
				aria-hidden="true"
				style:background="white"
				class={cn(
					SHEET,
					'w-[calc(100%-1.5rem)] transition-opacity duration-300',
					ready ? 'opacity-100' : 'opacity-0'
				)}
			></canvas>
		{:else if phase === 'html'}
			<div
				class="{SHEET} animate-in fade-in-0 bottom-0 overflow-hidden duration-300"
				style:background="white"
				bind:clientWidth={sheetWidth}
			>
				<EmailPreviewFrame
					html={`<div style="padding:40px">${html}</div>`}
					width={HTML_WIDTH}
					scale={(sheetWidth || 200) / HTML_WIDTH}
					title="Document preview"
					class="h-full"
				/>
			</div>
		{/if}
	{/if}
</div>
