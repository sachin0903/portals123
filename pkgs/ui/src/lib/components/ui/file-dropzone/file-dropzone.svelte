<script lang="ts" module>
	import { tv } from 'tailwind-variants';

	export const dropzoneVariants = tv({
		// THE LAYOUT LIVES ON `size`, not in the base: `icon` is a square chip and must not inherit the
		// default box's padding.
		base: 'rounded-xl border border-dashed text-center transition-colors outline-none',
		variants: {
			state: {
				idle: 'border-border text-muted-foreground hover:border-ring hover:bg-muted/40',
				dragging: 'border-ring bg-muted/60 text-foreground',
				disabled: 'border-border/60 text-muted-foreground/60 cursor-not-allowed'
			},
			/** `slim` is a COMPACT STACK for a narrow rail — title then limit, so the formats line is not
			 *  truncated mid-sentence the way a single row was. `icon` is a square chip that sits beside a
			 *  URL field on one line. The default box is 10rem of padding, which is most of a 320px style
			 *  rail's height for a control used once. */
			size: {
				default: 'flex w-full flex-col items-center justify-center gap-2 px-6 py-10 text-sm',
				slim: 'flex w-full flex-col items-center justify-center gap-0.5 px-3 py-2.5 text-xs',
				icon: 'flex size-8 shrink-0 items-center justify-center rounded-md p-0'
			}
		},
		defaultVariants: { state: 'idle', size: 'default' }
	});

	// The private-document backend's common document + raster-image allowlist. Extensions are used
	// because browsers routinely leave HEIC and legacy Office MIME types blank.
	export const DEFAULT_ACCEPT =
		'.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp,.txt,.csv,.rtf,.jpg,.jpeg,.png,.gif,.webp,.tif,.tiff,.bmp,.heic,.heif,.avif';
	export const DEFAULT_FORMATS = 'PDF, Office, text and common images';
	export const DEFAULT_MAX_BYTES = 25 * 1024 * 1024;
</script>

<script lang="ts">
	import UploadIcon from '@lucide/svelte/icons/upload';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import ImageIcon from '@lucide/svelte/icons/image';
	import XIcon from '@lucide/svelte/icons/x';
	import { cn } from '../../../utils.js';
	import { acceptFiles, fileKey, removeAt } from './files.js';
	import { formatBytes } from '../../../bytes.js';

	let {
		files = $bindable([]),
		name,
		accept = DEFAULT_ACCEPT,
		maxBytes = DEFAULT_MAX_BYTES,
		multiple = false,
		disabled = false,
		class: className,
		onreject,
		rich = false,
		size = 'default',
		title = 'Upload documents',
		formats,
		onaccept,
		collapseWhenFilled = false
	}: {
		/** Selected files (bindable). */
		files?: File[];
		/** Form field name. When set, the accepted file set participates in native form submission. */
		name?: string;
		/** Comma-separated accept list (also enforced here). */
		accept?: string;
		/** Per-file size cap in bytes. */
		maxBytes?: number;
		multiple?: boolean;
		disabled?: boolean;
		class?: string;
		/** Called with a human-readable reason when a file is rejected. */
		onreject?: (reason: string) => void;
		/** Larger presentation: icon tile, title, a "Choose files" button and an accepted-formats line. */
		rich?: boolean;
		/** `slim` is a compact title+limit stack for a narrow rail; `icon` is a square chip for a
		 *  one-line URL · upload row. The default's 10rem of padding is most of a rail's height. */
		size?: 'default' | 'slim' | 'icon';
		/** Heading shown in the rich / slim layouts; also the accessible name for `icon`. */
		title?: string;
		/** Overrides the accepted-formats line. */
		formats?: string;
		/** Called with the accepted set after a drop or a picker choice that actually added a file.
		 *  A rejection, or choosing a file already in the set, does not call it. */
		onaccept?: (files: File[]) => void;
		/** Once a file is chosen, the drop target gives way to the file list. The input stays, so a
		 *  form still submits the file. */
		collapseWhenFilled?: boolean;
	} = $props();

	// ONE CAP LABEL, from the shared helper. Both layouts used to round to MB, so a 512 KiB cap
	// advertised "1 MB" — a caller's own limit stated as twice itself, which is the number a user then
	// trusts. `formatBytes` is written for a FILE's size, where one decimal tells 24.6 MB from the
	// 25 MB cap; a cap is a round number by construction, so the trailing `.0` is dropped here rather
	// than by branching inside the shared helper.
	const sizeLabel = $derived(formatBytes(maxBytes).replace(/\.0(?= )/, ''));

	let dragging = $state(false);
	let inputEl: HTMLInputElement | null = $state(null);

	function syncInput(next: File[]): void {
		if (!inputEl || !name) return;
		const transfer = new DataTransfer();
		for (const file of next) transfer.items.add(file);
		inputEl.files = transfer.files;
	}

	function take(list: FileList | null): void {
		if (!list || disabled) return;
		const previous = files;
		const { files: next, rejections } = acceptFiles(files, Array.from(list), {
			accept,
			maxBytes,
			multiple
		});
		for (const reason of rejections) onreject?.(reason);
		files = next;
		// Keep the native input in step with the accumulated set. It cannot represent additions from
		// several picker/drop visits by itself, and clearing it here used to leave a form displaying
		// selected files while submitting none.
		syncInput(next);
		// `acceptFiles` returns the same array when nothing was taken, so a bad drop does not look
		// like a fresh choice to a caller that uploads on selection.
		if (next !== previous && next.length > 0) onaccept?.(next);
	}

	function onDrop(event: DragEvent): void {
		event.preventDefault();
		dragging = false;
		take(event.dataTransfer?.files ?? null);
	}

	function onChange(event: Event): void {
		take((event.currentTarget as HTMLInputElement).files);
	}

	function openPicker(): void {
		if (!disabled) inputEl?.click();
	}

	function remove(index: number): void {
		if (disabled) return;
		const next = removeAt(files, index);
		files = next;
		syncInput(next);
	}

	const zoneState = $derived(disabled ? 'disabled' : dragging ? 'dragging' : 'idle');
	const hasFiles = $derived(files.length > 0);
</script>

<!-- The chosen files are a SIBLING of the drop target, not content inside it: the target is a
     <button>, and a remove control has to be a real button of its own — which cannot be nested in
     one. Keeping them apart is also what makes the list a list (name, size, a control per row)
     rather than centred text, which is what several files need to be manageable. -->
<!-- `display: contents` for the chip: the wrapper exists only to hold the input and the list beside
     the target, and an `icon` dropzone has no list and sits in the caller's own one-line row, where a
     `w-full flex-col` wrapper would stretch it. -->
<div
	data-slot="file-dropzone-root"
	class={cn(size === 'icon' ? 'contents' : 'flex w-full flex-col gap-2', className)}
>
	<!-- Outside the drop target on purpose: `inputEl.click()` on a child of the <button> dispatches a
	     click that bubbles straight back into `openPicker`. -->
	<input
		bind:this={inputEl}
		type="file"
		{name}
		{accept}
		{multiple}
		{disabled}
		class="hidden"
		onchange={onChange}
	/>

	{#if !(collapseWhenFilled && hasFiles)}
		<button
			type="button"
			{disabled}
			data-slot="file-dropzone"
			class={dropzoneVariants({ state: zoneState, size: rich ? 'default' : size })}
			title={size === 'icon'
				? `${dragging ? 'Drop to upload' : title} · ${formats ?? DEFAULT_FORMATS} · up to ${sizeLabel}`
				: undefined}
			aria-label={size === 'icon' ? title : undefined}
			onclick={openPicker}
			ondragover={(e) => {
				e.preventDefault();
				if (!disabled) dragging = true;
			}}
			ondragleave={() => (dragging = false)}
			ondrop={onDrop}
		>
			{#if rich}
				<span
					class="bg-primary/10 text-primary mb-1 flex size-12 items-center justify-center rounded-xl"
				>
					{#if hasFiles}
						<!-- files[0]? not files[0]: noUncheckedIndexedAccess types the index as possibly
					     undefined even under the hasFiles guard, and svelte-check fails on it. -->
						{#if files[0]?.type.startsWith('image/')}
							<ImageIcon class="size-5" />
						{:else}
							<FileTextIcon class="size-5" />
						{/if}
					{:else}
						<UploadIcon class="size-5" />
					{/if}
				</span>
				<span class="text-foreground text-sm font-semibold">
					{hasFiles
						? files.length === 1
							? '1 file selected'
							: `${files.length} files selected`
						: title}
				</span>
				<span class="text-muted-foreground text-xs">
					{dragging
						? 'Drop to upload'
						: !hasFiles
							? 'Drag and drop files here, or click to browse'
							: multiple
								? 'Drag and drop to add more, or click to browse'
								: 'Drag and drop to replace, or click to change'}
				</span>
				<span
					class="bg-primary text-primary-foreground mt-2 inline-flex items-center rounded-lg px-4 py-2 text-sm font-medium"
				>
					{!hasFiles ? 'Choose files' : multiple ? 'Add more files' : 'Choose a different file'}
				</span>
				<span class="text-muted-foreground/80 mt-1 text-[11px]">
					Accepted formats: {formats ?? DEFAULT_FORMATS} · Max {sizeLabel} per file
				</span>
			{:else if size === 'icon'}
				<!-- SQUARE CHIP: sits beside the URL field on one line; the formats and the cap ride in the
			     tooltip and the accessible name above, since there is no room to print them. -->
				<UploadIcon class="size-3.5 opacity-70" />
			{:else if size === 'slim'}
				<!-- COMPACT STACK: one row truncated the limit mid-sentence in a 320px rail ("up to 512…"). -->
				<span class="inline-flex items-center gap-1.5 font-medium">
					<UploadIcon class="size-3.5 shrink-0 opacity-70" />
					{dragging ? 'Drop to upload' : title}
				</span>
				<span class="text-muted-foreground text-[0.65rem] leading-tight">
					{formats ?? DEFAULT_FORMATS} · up to {sizeLabel}
				</span>
			{:else}
				<UploadIcon class="size-6 opacity-70" />
				<span class="font-medium">
					{dragging ? 'Drop to upload' : 'Drag a file here, or click to browse'}
				</span>
				<span class="text-muted-foreground text-xs">
					{formats ?? DEFAULT_FORMATS} · up to {sizeLabel}
				</span>
			{/if}
		</button>
	{/if}

	<!-- Never under the chip: an `icon` dropzone is one control in a row, and a list under it would
	     push that row apart. Its caller shows what was chosen in its own way. -->
	{#if hasFiles && size !== 'icon'}
		<ul data-slot="file-dropzone-files" class="flex w-full flex-col gap-1">
			{#each files as file, index (fileKey(file))}
				<li
					class="bg-muted/40 flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs"
					data-slot="file-dropzone-file"
				>
					{#if file.type.startsWith('image/')}
						<ImageIcon class="text-muted-foreground size-4 shrink-0" />
					{:else}
						<FileTextIcon class="text-muted-foreground size-4 shrink-0" />
					{/if}
					<span class="text-foreground min-w-0 flex-1 truncate font-medium">{file.name}</span>
					<span class="text-muted-foreground shrink-0 tabular-nums">{formatBytes(file.size)}</span>
					<button
						type="button"
						{disabled}
						data-slot="file-dropzone-remove"
						class="text-muted-foreground hover:bg-background hover:text-foreground focus-visible:ring-ring -mr-1 shrink-0 rounded-md p-1 transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
						aria-label={`Remove ${file.name}`}
						onclick={() => remove(index)}
					>
						<XIcon class="size-3.5" />
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
