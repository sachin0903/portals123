<script lang="ts">
	/**
	 * SOURCE AS ONE LINE: URL/merge field · upload chip.
	 *
	 * A modal was the wrong shape for this: choosing a picture is one property of the selected element,
	 * and the rail already holds its alternative text and its fallback. Putting the choice behind a
	 * dialog meant covering the canvas to answer a question about it, and it needed a link field of its
	 * own beside the one already in the rail — two boxes for one property.
	 *
	 * DELIBERATELY NOT A LIBRARY. It listed the tenant bucket's public prefix and drew the firm's stored
	 * images as a grid of thumbnails — which cost most of a 320px rail's height, and answered a question
	 * ("which of my images?") that cannot be asked properly until per-firm image storage exists. So this
	 * is one upload control until then; browsing comes back with that work, not before it.
	 *
	 * THE FIRM LOGO IS NOT THIS CONTROL. The masthead is a bespoke block (`brand.logo_url` + wordmark
	 * fallback) while brand storage is still being stood up — uploading here would fight that path. This
	 * line is for content images: paste an https URL, or drop a file into the chip.
	 *
	 * The upload still POSTs to the app's proxy, and the answer says whether the object is HOSTED. An
	 * unhosted one is stored and embedded — usable in the editor, invisible in most inboxes — which is
	 * an outcome to state, not a reason to refuse the file.
	 */
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import FileDropzone from '../../ui/file-dropzone/file-dropzone.svelte';
	import { Input } from '../../ui/input/index.js';

	let {
		src = '',
		libraryUrl = null,
		onpick,
		onchange
	}: {
		/** Current source — drives the URL field. */
		src?: string;
		/**
		 * The app's proxy the upload POSTs to (multipart `file`). `null` ⇒ no upload chip, leaving the
		 * URL field as the whole affordance rather than an upload with nowhere to go.
		 */
		libraryUrl?: string | null;
		/** Uploaded. Applied straight to the element — there is no Use-image step. */
		onpick?: (url: string) => void;
		/** Typed or pasted into the URL field. */
		onchange?: (url: string) => void;
	} = $props();

	let uploading = $state(false);
	let uploadError = $state<string | null>(null);
	let files = $state<File[]>([]);

	// An upload starts as soon as a file is taken, rather than behind a second button: the file WAS
	// the action, and a "Choose file" that then needs "Upload" is the step everyone forgets.
	$effect(() => {
		const file = files[0];
		if (file) void upload(file);
	});

	async function errText(res: Response, fallback: string): Promise<string> {
		const msg = await res
			.clone()
			.json()
			.then((b) => b?.message ?? b?.detail)
			.catch(() => null);
		return typeof msg === 'string' && msg ? msg : `${fallback} (${res.status})`;
	}

	async function upload(file: File): Promise<void> {
		if (!libraryUrl || uploading) return;
		uploading = true;
		uploadError = null;
		files = [];
		try {
			const body = new FormData();
			body.append('file', file, file.name);
			const res = await fetch(libraryUrl, { method: 'POST', body });
			if (!res.ok) {
				uploadError = await errText(res, 'Upload failed');
				return;
			}
			const added = (await res.json()) as { url?: string; hosted?: boolean };
			// APPLIED EITHER WAY, and the unhosted case says why it will not arrive rather than refusing
			// the file: refusing left an author with an upload control that never accepted anything.
			if (added.hosted === false)
				uploadError =
					'Added to the editor. Use a publicly hosted image to ensure it appears in email.';
			if (added.url) onpick?.(added.url);
		} catch (e) {
			uploadError = e instanceof Error ? e.message : 'Upload failed';
		} finally {
			uploading = false;
		}
	}

	const uploadLabel = $derived(uploading ? 'Uploading…' : 'Add an image');
</script>

<div class="flex flex-col gap-1.5">
	<!-- ONE LINE: URL · upload. The firm logo uses the masthead block, not this. -->
	<div class="flex items-center gap-2">
		<Input
			id="et-img-src"
			class="h-8 min-w-0 flex-1"
			value={src}
			placeholder="https://…"
			spellcheck="false"
			oninput={(e) => onchange?.(e.currentTarget.value)}
		/>
		{#if libraryUrl}
			<FileDropzone
				bind:files
				size="icon"
				accept="image/png,image/jpeg"
				maxBytes={512 * 1024}
				disabled={uploading}
				title={uploadLabel}
				formats="PNG or JPEG"
				onreject={(reason) => (uploadError = reason)}
			/>
		{/if}
	</div>

	{#if uploadError}
		<Alert variant="destructive">
			<AlertDescription class="text-xs">{uploadError}</AlertDescription>
		</Alert>
	{/if}
</div>
