<script lang="ts">
	import { Button } from '../../ui/button/index.js';
	import * as Dialog from '../../ui/dialog/index.js';
	import { FileDropzone } from '../../ui/file-dropzone/index.js';
	import { Label } from '../../ui/label/index.js';
	import * as Select from '../../ui/select/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import type { ClientOption, DocumentTypeOption } from './documents.js';

	let {
		open = $bindable(false),
		presetFiles = null,
		clients = [],
		documentTypes = [],
		onupload
	}: {
		open?: boolean;
		presetFiles?: File[] | null;
		clients?: ClientOption[];
		documentTypes?: DocumentTypeOption[];
		onupload: (input: {
			kind: 'document' | 'file';
			files: File[];
			documentType: string;
			subjectContactId: number | null;
		}) => Promise<boolean>;
	} = $props();

	let files = $state<File[]>([]);
	let kind = $state<'document' | 'file'>('document');
	let documentType = $state('');
	let clientId = $state('');
	let uploading = $state(false);
	let error = $state<string | null>(null);
	const availableTypes = $derived(documentTypes.filter((type) => type.kind === kind));

	$effect(() => {
		if (open && presetFiles?.length && files.length === 0) files = presetFiles;
	});

	function reset() {
		files = [];
		kind = 'document';
		documentType = '';
		clientId = '';
		uploading = false;
		error = null;
	}

	async function submit() {
		if (!files.length || !documentType || uploading) return;
		uploading = true;
		error = null;
		try {
			const ok = await onupload({
				kind,
				files: [...files],
				documentType,
				subjectContactId: clientId ? Number(clientId) : null
			});
			if (!ok) {
				error = 'The upload could not be completed.';
				return;
			}
			open = false;
			reset();
		} finally {
			uploading = false;
		}
	}
</script>

<Dialog.Root bind:open onOpenChange={(next) => !next && reset()}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Upload documents</Dialog.Title>
			<Dialog.Description>Add files to the live document library.</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-4 py-2">
			<Segmented
				options={[
					{ value: 'document', label: 'Document' },
					{ value: 'file', label: 'Supporting file' }
				]}
				value={kind}
				onValueChange={(value) => {
					kind = value as 'document' | 'file';
					documentType = '';
				}}
			/>
			<FileDropzone bind:files multiple rich />
			<div class="space-y-1.5">
				<Label for="document-upload-type">Type</Label>
				<Select.Root type="single" bind:value={documentType}>
					<Select.Trigger id="document-upload-type" class="w-full">
						{availableTypes.find((type) => type.slug === documentType)?.label ?? 'Choose a type…'}
					</Select.Trigger>
					<Select.Content>
						{#each availableTypes as type (type.slug)}
							<Select.Item value={type.slug}>{type.label}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
			<div class="space-y-1.5">
				<Label for="document-upload-client">Client (optional)</Label>
				<Select.Root type="single" bind:value={clientId}>
					<Select.Trigger id="document-upload-client" class="w-full">
						{clients.find((client) => String(client.contact_id) === clientId)?.name ?? 'No client'}
					</Select.Trigger>
					<Select.Content>
						<Select.Item value="">No client</Select.Item>
						{#each clients as client (client.contact_id)}
							<Select.Item value={String(client.contact_id)}>
								{client.name ?? client.email ?? `Client ${client.contact_id}`}
							</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
			</div>
			{#if error}<p class="text-destructive text-sm" role="alert">{error}</p>{/if}
		</div>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (open = false)} disabled={uploading}>Cancel</Button>
			<Button disabled={!files.length || !documentType || uploading} onclick={submit}>
				{uploading ? 'Uploading…' : 'Upload'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
