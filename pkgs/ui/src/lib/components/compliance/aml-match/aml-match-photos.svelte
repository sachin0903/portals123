<script lang="ts">
	// A screening match's photos, one at a time, with arrows and dots for the rest, and a click opens
	// one large in a pop-up over the page. The AML console's match profile and a firm's compliance
	// tab show them alike. Third-party photos break often: one that fails to load drops out and the
	// next takes its place, and with none left nothing is shown at all.
	import { SvelteSet } from 'svelte/reactivity';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { ImageViewer } from '../../ui/image-viewer/index.js';
	import { isWebUrl } from '../../../urls.js';

	let {
		images,
		name,
		class: className = ''
	}: {
		/** The match's photos, on third-party sites. */
		images: string[];
		/** Who they are of: the photos' label. */
		name: string;
		class?: string;
	} = $props();

	const failed = new SvelteSet<string>();
	const photos = $derived(
		images.filter((url, i, all) => isWebUrl(url) && !failed.has(url) && all.indexOf(url) === i)
	);

	// Held to the photos still loading, so a photo that drops out hands its place to the next (or
	// the last, at the end).
	let shown = $state(0);
	const at = $derived(Math.min(shown, Math.max(photos.length - 1, 0)));
	const photo = $derived(photos[at] ?? null);

	/** Moves through the photos, round from the last to the first. */
	function step(by: number) {
		shown = (at + by + photos.length) % photos.length;
	}

	/** The pop-up, open on the photo it was opened from. */
	let viewing = $state<number | null>(null);

	/** An arrow over the side of the photo. */
	const ARROW =
		'bg-background/75 text-foreground hover:bg-background focus-visible:ring-ring/50 absolute top-1/2 flex size-7 -translate-y-1/2 cursor-pointer items-center justify-center rounded-full border shadow-sm backdrop-blur-sm outline-none focus-visible:ring-[3px]';
</script>

{#if photo}
	<div class={['w-full max-w-56 shrink-0 space-y-1.5', className]}>
		<div class="relative">
			<!-- Third-party photos open large in a pop-up; the image request tells the site nothing about
			     this page. -->
			<button
				type="button"
				aria-label={photos.length > 1
					? `View the photo of ${name} (${at + 1} of ${photos.length})`
					: `View the photo of ${name}`}
				class="focus-visible:ring-ring/50 block w-full shrink-0 cursor-zoom-in rounded-lg outline-none focus-visible:ring-[3px]"
				onclick={() => (viewing = at)}
			>
				<img
					src={photo}
					alt={`Photo of ${name}`}
					referrerpolicy="no-referrer"
					decoding="async"
					class="border-border bg-muted/40 aspect-[4/3] w-full rounded-lg border object-cover"
					onerror={() => failed.add(photo)}
				/>
			</button>
			{#if photos.length > 1}
				<button
					type="button"
					aria-label="Previous photo"
					class="{ARROW} left-1.5"
					onclick={() => step(-1)}
				>
					<ChevronLeftIcon class="size-4" />
				</button>
				<button
					type="button"
					aria-label="Next photo"
					class="{ARROW} right-1.5"
					onclick={() => step(1)}
				>
					<ChevronRightIcon class="size-4" />
				</button>
			{/if}
		</div>
		{#if photos.length > 1}
			<div class="flex justify-center gap-1" aria-hidden="true">
				{#each photos as url, i (url)}
					<span
						class={[
							'size-1.5 rounded-full',
							i === at ? 'bg-foreground/70' : 'bg-muted-foreground/30'
						]}
					></span>
				{/each}
			</div>
		{/if}
	</div>
{/if}

{#if viewing !== null && photos.length}
	<ImageViewer
		images={photos.map((url) => ({ url, label: name }))}
		index={Math.min(viewing, photos.length - 1)}
		onclose={() => (viewing = null)}
	/>
{/if}
