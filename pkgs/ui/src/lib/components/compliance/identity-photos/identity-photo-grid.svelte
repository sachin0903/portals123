<script module lang="ts">
	import type { ViewerImage } from '../../ui/image-viewer/index.js';

	/** One photo of an identity check, as the viewer shows it, with an optional note on its tile. */
	export type IdentityPhoto = ViewerImage & { note?: string | null };
</script>

<script lang="ts">
	// A client's identity photos as tiles of one size, each photo whole inside its tile: never
	// cropped, never stretched. A click opens one large (the caller's ImageViewer); a PDF scan shows
	// as a file tile, since an <img> cannot draw it. The AML console and the firm's compliance tab
	// show a check's photos alike. `size` sets the tiles: `compact` is short, for a page that has to
	// fit one screen; `large` is tall and shares the row between the photos (at most three to a row),
	// so they fill the space they have, for a page with height to spare.
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import Maximize2Icon from '@lucide/svelte/icons/maximize-2';

	let {
		photos,
		onopen,
		size = 'default'
	}: {
		photos: IdentityPhoto[];
		onopen: (index: number) => void;
		size?: 'compact' | 'default' | 'large';
	} = $props();

	const tile = $derived(size === 'compact' ? 'h-24' : size === 'large' ? 'h-52' : 'h-32');
	/** A large grid's columns: one per photo, up to three, so the photos fill the row. */
	const LARGE_COLUMNS = ['grid-cols-1', 'grid-cols-2', 'grid-cols-2 @xl:grid-cols-3'];
</script>

<div class="@container">
	<ul
		class={[
			'grid gap-2',
			size === 'large'
				? LARGE_COLUMNS[Math.min(photos.length, 3) - 1]
				: 'grid-cols-2 @md:grid-cols-3 @3xl:grid-cols-4 @5xl:grid-cols-6'
		]}
	>
		{#each photos as photo, i (photo.url)}
			<li class="min-w-0">
				<button
					type="button"
					aria-label={`View ${photo.label}`}
					class="group bg-muted/40 hover:border-ring/60 focus-visible:ring-ring/50 block w-full cursor-zoom-in overflow-hidden rounded-lg border outline-none focus-visible:ring-[3px]"
					onclick={() => onopen(i)}
				>
					{#if photo.contentType === 'application/pdf'}
						<span
							class="text-muted-foreground flex {tile} w-full flex-col items-center justify-center gap-1 text-xs"
						>
							<FileTextIcon class="size-7" aria-hidden="true" />
							PDF
						</span>
					{:else}
						<img
							src={photo.url}
							alt={photo.label}
							referrerpolicy="no-referrer"
							decoding="async"
							class="{tile} w-full object-contain"
						/>
					{/if}
					<span
						class="text-muted-foreground group-hover:text-foreground flex items-center justify-between gap-1 border-t px-2 py-1 text-left text-[0.7rem]"
					>
						<span class="truncate">{photo.label}{photo.note ? ` · ${photo.note}` : ''}</span>
						<Maximize2Icon class="size-3 shrink-0" aria-hidden="true" />
					</span>
				</button>
			</li>
		{/each}
	</ul>
</div>
