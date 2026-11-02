<script lang="ts">
	import { deserialize } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from '@graphene/ui';
	import {
		DocumentsLibrary,
		DocumentUploadDialog,
		type DocumentsLibraryActions
	} from '@graphene/ui/book/documents';
	import { paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/operate/documents`);
	let uploadOpen = $state(false);
	let presetFiles = $state<File[] | null>(null);

	function navigate(next: ListingQuery) {
		const query = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(query ? `?${query}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
	async function post(action: string, uid: string): Promise<boolean> {
		const body = new FormData();
		body.append('uid', uid);
		const response = await fetch(action, { method: 'POST', body });
		return response.ok;
	}
	const actions: DocumentsLibraryActions | null = $derived(
		data.writing
			? {
					archive: async (uids) => {
						const results = await Promise.all(uids.map((uid) => post('?/archive', uid)));
						if (results.every(Boolean)) toast.success('Document deleted.');
						else toast.error('Some documents could not be deleted.');
						await invalidateAll();
					},
					deleteFiles: async (uids) => {
						const results = await Promise.all(uids.map((uid) => post('?/deleteFile', uid)));
						if (results.every(Boolean)) toast.success('File deleted.');
						else toast.error('Some files could not be deleted.');
						await invalidateAll();
					},
					retryRender: async (uid) => {
						if (await post('?/retryRender', uid)) toast.success('Render queued.');
						else toast.error('Could not queue the render.');
						await invalidateAll();
					},
					upload: (preset) => {
						presetFiles = preset ?? null;
						uploadOpen = true;
					}
				}
			: null
	);
	async function upload(input: {
		kind: 'document' | 'file';
		files: File[];
		documentType: string;
		subjectContactId: number | null;
	}): Promise<boolean> {
		const body = new FormData();
		for (const file of input.files) body.append('file', file, file.name);
		body.append(
			input.kind === 'document' ? 'document_type' : 'attachment_type',
			input.documentType
		);
		if (input.subjectContactId != null) {
			body.append(
				input.kind === 'document' ? 'subject_contact_id' : 'contact_id',
				String(input.subjectContactId)
			);
		}
		const response = await fetch(input.kind === 'document' ? '?/upload' : '?/uploadFile', {
			method: 'POST',
			body
		});
		const result = deserialize(await response.text());
		if (result.type !== 'success') {
			toast.error(
				result.type === 'failure'
					? String(result.data?.error ?? 'Upload failed.')
					: 'Upload failed.'
			);
			return false;
		}
		await invalidateAll();
		toast.success('Uploaded.');
		return true;
	}
</script>

<svelte:head><title>Documents · {data.tenant.name}</title></svelte:head>

<DocumentsLibrary
	documents={data.documents}
	activity={data.activity}
	canReadActivity={!data.activityFailed}
	loadError={data.loadError}
	listing={data.query}
	facets={data.facets}
	facetLabels={data.facetLabels}
	total={data.total}
	onquery={navigate}
	links={{
		document: (uid) => `${base}/${uid}`,
		documentContent: (uid) => `${base}/${uid}/content`,
		fileDownload: (uid) => `${base}/files/${uid}/download`,
		client: (uid) => `/tenants/${data.tenant.uid}/operate/clients/${uid}`,
		requests: `${base}/requests`,
		templates: `/tenants/${data.tenant.uid}/operate/templates`
	}}
	{actions}
	onretry={() => invalidateAll()}
	onnavigate={(href) => goto(href)}
/>

{#if data.writing}
	<DocumentUploadDialog
		bind:open={uploadOpen}
		{presetFiles}
		clients={data.clients}
		documentTypes={data.documentTypes}
		onupload={upload}
	/>
{/if}
