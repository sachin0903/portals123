<script lang="ts">
	// The signed W-9 or W-8, under the Compliance card's answer. The dropzone's input is named, so
	// the card's own Save posts the file and the app uploads it once the answer is saved. A blank
	// answer or another stored code takes no form, so the slot is not shown for it.
	import { toast } from 'svelte-sonner';
	import { FileDropzone } from '../../ui/file-dropzone/index.js';
	import { LINK_RECORD } from '../../../text-shell.js';
	import { fatcaFormLabel, takesFatcaDocument } from './person-facts.js';

	let {
		status,
		savedStatus,
		document,
		href
	}: {
		/** The answer on the form now. */
		status: string;
		/** The answer the record holds. The linked file belongs to this one. */
		savedStatus: string | null | undefined;
		/** The file linked to the saved answer. */
		document: { uid: string; name: string } | null | undefined;
		/** Opens a document. Absent where the app has no document route: the name is shown alone. */
		href?: (uid: string) => string;
	} = $props();

	let files = $state<File[]>([]);
	const form = $derived(fatcaFormLabel(status));
	/** A changed answer unlinks the file on save, so the file shows only while the answer stands. */
	const current = $derived(status === savedStatus ? (document ?? null) : null);
</script>

{#if takesFatcaDocument(status)}
	<div class="min-w-0 space-y-1">
		<p class="text-sm leading-none font-medium">Signed {form}</p>
		{#if current}
			<p class="text-xs">
				{#if href}
					<a href={href(current.uid)} class="font-medium {LINK_RECORD}">{current.name}</a>
				{:else}
					<span class="font-medium">{current.name}</span>
				{/if}
			</p>
		{/if}
		<FileDropzone
			name="fatca_document"
			bind:files
			size="slim"
			title={current ? `Replace the signed ${form}` : `Upload the signed ${form}`}
			accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.tif,.tiff,.bmp,.heic,.heif,.avif"
			formats="A PDF scan or a photograph"
			onreject={(reason) => toast.error(reason)}
		/>
	</div>
{/if}
