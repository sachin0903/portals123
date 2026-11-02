<script lang="ts">
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import { formatBytes } from '../../../bytes.js';
	import { cn } from '../../../utils.js';

	/** A captured file shown as its name and size rather than as a picture: a PDF scan has no
	 *  image to preview. */
	let { file, class: className }: { file: File; class?: string } = $props();
</script>

<div
	data-slot="captured-file-card"
	class={cn(
		'bg-background flex min-w-0 items-center gap-3 rounded-lg border px-3 py-2.5',
		className
	)}
>
	<FileTextIcon class="text-muted-foreground size-5 shrink-0" aria-hidden="true" />
	<p class="min-w-0 truncate text-sm">{file.name}, {formatBytes(file.size)}</p>
</div>
