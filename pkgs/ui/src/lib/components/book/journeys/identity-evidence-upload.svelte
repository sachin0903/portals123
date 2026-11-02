<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { FileDropzone } from '../../ui/file-dropzone/index.js';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import type { IdentityDocument } from './steps.js';

	let {
		item,
		action,
		caseUid,
		stepKey,
		participantContactId,
		enhance
	}: {
		item: IdentityDocument;
		action: string;
		caseUid: string;
		stepKey: string;
		participantContactId: number | null;
		enhance: Enhance;
	} = $props();

	// One component instance per evidence item means each dropzone owns a real File[] from its first
	// render. Binding a dropzone to an absent key in a shared object threw during hydration, taking
	// down the whole journey page precisely when the manual-IDV block first appeared.
	let files = $state<File[]>([]);
	let pending = $state(false);
	let formEl = $state<HTMLFormElement>();

	// Choosing the file IS the upload. The input is synced before this runs, so the submission
	// carries the file without a second click.
	function uploadChosen(): void {
		if (pending || !formEl) return;
		formEl.requestSubmit();
	}
</script>

<form
	bind:this={formEl}
	method="POST"
	enctype="multipart/form-data"
	{action}
	use:enhance={withToast('Identity evidence uploaded', {
		pending: (value) => (pending = value),
		onSuccess: () => (files = [])
	})}
	class="mt-3 space-y-2"
>
	<input type="hidden" name="case_uid" value={caseUid} />
	<input type="hidden" name="step_key" value={stepKey} />
	<input type="hidden" name="participant_contact_id" value={participantContactId ?? ''} />
	<input type="hidden" name="item_uid" value={item.item_uid} />
	<FileDropzone
		name="file"
		bind:files
		title={item.title}
		accept="application/pdf,image/jpeg,image/png"
		formats={item.format_hint}
		disabled={pending}
		collapseWhenFilled
		onreject={(reason) => toast.error(reason)}
		onaccept={uploadChosen}
	/>
	<p class="text-muted-foreground text-xs">
		{pending ? 'Uploading…' : `${item.format_hint}, up to 25 MB`}
	</p>
</form>
