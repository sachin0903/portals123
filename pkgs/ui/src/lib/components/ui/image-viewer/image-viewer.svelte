<script lang="ts" module>
	/** One image the viewer can show: a screening match's photo, or an identity check's file. A
	 *  `contentType` of `application/pdf` is drawn by the document viewer. */
	export type ViewerImage = { url: string; label: string; contentType?: string | null };
</script>

<script lang="ts">
	// One image large, in a pop-up over the page: a click on a photo never leaves the page. With
	// several, the arrows (buttons or the Left / Right keys) and the thumbnails move between them. An
	// identity side sent as a PDF is drawn by the document viewer. "Open original" is the one way out,
	// for a reader who needs the source itself. Every image is fetched without a referrer: match
	// photos are third-party, and identity files must not pass on where they were seen.
	// The parent mounts this only while it is open, so each opening starts fresh.
	import { untrack } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ExternalLinkIcon from '@lucide/svelte/icons/external-link';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import ImageOffIcon from '@lucide/svelte/icons/image-off';
	import { Button } from '../button/index.js';
	import * as Dialog from '../dialog/index.js';
	import { DocumentViewer } from '../document-viewer/index.js';

	let {
		images,
		index = 0,
		onclose
	}: {
		images: ViewerImage[];
		/** The image shown first. */
		index?: number;
		onclose: () => void;
	} = $props();

	let current = $state(untrack(() => index));
	const at = $derived(Math.min(Math.max(current, 0), images.length - 1));
	const image = $derived<ViewerImage | undefined>(images[at]);
	const several = $derived(images.length > 1);

	/** Images that would not load: each shows a short message in its place. */
	const failed = new SvelteSet<string>();

	function pdf(file: ViewerImage): boolean {
		return file.contentType === 'application/pdf';
	}

	/** Moves through the images, round from the last to the first. */
	function step(by: number) {
		current = (at + by + images.length) % images.length;
	}

	function onkeydown(event: KeyboardEvent) {
		if (!several) return;
		// The PDF viewer's search box keeps its own arrow keys.
		if (event.target instanceof Element && event.target.closest('input, textarea, select')) return;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			step(-1);
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			step(1);
		}
	}
</script>

<Dialog.Root open onOpenChange={(open) => !open && onclose()}>
	<Dialog.Content class="sm:max-w-4xl" {onkeydown}>
		{#if image}
			<Dialog.Header class="pr-8">
				<Dialog.Title class="truncate leading-normal" title={image.label}
					>{image.label}</Dialog.Title
				>
				{#if several}
					<Dialog.Description class="tabular-nums">{at + 1} of {images.length}</Dialog.Description>
				{/if}
			</Dialog.Header>

			<div class="bg-muted/30 flex min-h-48 items-center justify-center overflow-hidden rounded-lg">
				{#if pdf(image)}
					<DocumentViewer
						src={image.url}
						contentType="application/pdf"
						name={image.label}
						class="h-[75vh]"
					/>
				{:else if failed.has(image.url)}
					<p class="text-muted-foreground flex items-center gap-2 p-8 text-sm">
						<ImageOffIcon class="size-4 shrink-0" aria-hidden="true" />
						This image could not be loaded.
					</p>
				{:else}
					{#key image.url}
						<img
							src={image.url}
							alt={image.label}
							referrerpolicy="no-referrer"
							decoding="async"
							class="max-h-[75vh] w-full object-contain"
							onerror={() => failed.add(image.url)}
						/>
					{/key}
				{/if}
			</div>

			{#if several}
				<div class="flex items-center gap-2">
					<Button
						variant="outline"
						size="icon-sm"
						class="shrink-0"
						aria-label="Previous image"
						onclick={() => step(-1)}
					>
						<ChevronLeftIcon />
					</Button>
					<ul class="flex min-w-0 flex-1 justify-center-safe gap-2 overflow-x-auto p-1">
						{#each images as file, i (i)}
							<li class="shrink-0">
								<button
									type="button"
									aria-label={`Show ${file.label}, ${i + 1} of ${images.length}`}
									aria-current={i === at ? 'true' : undefined}
									class={[
										'bg-muted/40 focus-visible:ring-ring/50 flex size-14 cursor-pointer items-center justify-center overflow-hidden rounded-md border outline-none focus-visible:ring-[3px]',
										i === at ? 'border-primary ring-primary ring-1' : 'opacity-70 hover:opacity-100'
									]}
									onclick={() => (current = i)}
								>
									{#if pdf(file)}
										<FileTextIcon class="text-muted-foreground size-5" aria-hidden="true" />
									{:else if failed.has(file.url)}
										<ImageOffIcon class="text-muted-foreground size-5" aria-hidden="true" />
									{:else}
										<img
											src={file.url}
											alt=""
											referrerpolicy="no-referrer"
											loading="lazy"
											decoding="async"
											class="size-full object-cover"
											onerror={() => failed.add(file.url)}
										/>
									{/if}
								</button>
							</li>
						{/each}
					</ul>
					<Button
						variant="outline"
						size="icon-sm"
						class="shrink-0"
						aria-label="Next image"
						onclick={() => step(1)}
					>
						<ChevronRightIcon />
					</Button>
				</div>
			{/if}

			<Dialog.Footer class="py-3">
				<!-- The source itself, for a reader who needs it: a new tab that learns nothing about
				     this page. -->
				<a
					href={image.url}
					target="_blank"
					rel="noopener noreferrer"
					referrerpolicy="no-referrer"
					class="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs hover:underline"
				>
					Open original <ExternalLinkIcon class="size-3" aria-hidden="true" />
				</a>
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>
