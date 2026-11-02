<script lang="ts">
	import {
		Alert,
		AlertDescription,
		LoadErrorBanner,
		PageHeader,
		BrandingEditor,
		emailCopyFor,
		RecordSkeleton,
		Streamed,
		type BrandingEditorValue,
		type ClassificationDraft,
		type GroupDraft
	} from '@graphene/ui';
	import { applyAction, deserialize } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { ActionResult } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let saving = $state(false);
	let uploading = $state(false);
	let error = $state<string | null>(null);
	let saved = $state(false);

	async function save(next: {
		name: string;
		client: BrandingEditorValue;
		platform: BrandingEditorValue;
	}) {
		saving = true;
		error = null;
		saved = false;
		const body = new FormData();
		body.append('name', next.name);
		body.append('client', JSON.stringify(next.client));
		body.append('platform', JSON.stringify(next.platform));
		const res = await fetch('?/save', { method: 'POST', body });
		const result: ActionResult = deserialize(await res.text());
		saving = false;
		if (result.type === 'success') {
			saved = true;
			await invalidateAll();
		} else if (result.type === 'failure') {
			error = (result.data?.error as string) ?? 'Could not save branding.';
		} else {
			await applyAction(result);
		}
	}

	// The API scopes assets per tenant, not per portal, so `portal` is accepted for the interface but
	// not sent — a logo uploaded once is available to both portals' branding to reference.
	async function upload(
		_portal: 'client' | 'advisor',
		kind: 'logo' | 'logo-dark' | 'icon' | 'favicon' | 'card-art',
		file: File
	): Promise<string> {
		uploading = true;
		error = null;
		try {
			const body = new FormData();
			body.append('file', file, file.name);
			// An SVG logo/icon's PNG for email — Outlook shows the SVG itself as a file attachment.
			const emailCopy = await emailCopyFor(kind, file);
			if (emailCopy) body.append('email_copy', emailCopy, 'email-copy.png');
			const res = await fetch(`?/upload&kind=${encodeURIComponent(kind)}`, {
				method: 'POST',
				body
			});
			const result: ActionResult = deserialize(await res.text());
			if (result.type === 'success' && result.data?.url) return result.data.url as string;
			if (result.type === 'failure') {
				error = (result.data?.error as string) ?? 'Upload failed.';
			}
			throw new Error(error ?? 'Upload failed.');
		} finally {
			uploading = false;
		}
	}

	async function postClassification(action: string, body: FormData) {
		const res = await fetch(`?/${action}`, { method: 'POST', body });
		const result: ActionResult = deserialize(await res.text());
		if (result.type === 'success') {
			await invalidateAll();
			return;
		}
		if (result.type === 'failure') {
			throw new Error((result.data?.error as string) ?? 'Could not save that book.');
		}
		await applyAction(result);
	}

	async function saveClassification(draft: ClassificationDraft) {
		const body = new FormData();
		if (draft.uid) body.append('uid', draft.uid);
		body.append(
			'classification',
			JSON.stringify({
				name: draft.name,
				slug: draft.slug || null,
				sort_order: Number(draft.sort_order) || 0,
				is_default: draft.is_default,
				match: draft.match,
				capabilities: draft.capabilities
			})
		);
		await postClassification('saveClassification', body);
	}

	async function deleteClassification(uid: string) {
		const body = new FormData();
		body.append('uid', uid);
		await postClassification('deleteClassification', body);
	}

	async function saveGroup(draft: GroupDraft) {
		const body = new FormData();
		if (draft.uid) body.append('uid', draft.uid);
		body.append(
			'group',
			JSON.stringify({
				name: draft.name,
				slug: draft.slug || null,
				description: draft.description,
				sort_order: Number(draft.sort_order) || 0
			})
		);
		await postClassification('saveGroup', body);
	}

	async function deleteGroup(uid: string) {
		const body = new FormData();
		body.append('uid', uid);
		await postClassification('deleteGroup', body);
	}
</script>

<PageHeader
	title="Studio"
	description="Logos and accents on the adviser portal. The client portal is the shopfront."
/>

{#snippet notAuthorised()}
	<Alert variant="destructive">
		<AlertDescription>
			You do not have permission to manage your firm's branding. Ask a firm administrator for
			access.
		</AlertDescription>
	</Alert>
{/snippet}

{#if !data.authorised || !data.studio}
	{@render notAuthorised()}
{:else}
	<!-- `keep`: a save re-runs the load, and the editor holds its place while the fresh branding
	     lands rather than dropping back to the placeholder. -->
	<Streamed data={data.studio} onretry={() => invalidateAll()} keep>
		{#snippet pending()}
			<RecordSkeleton kpis={0} cards={2} header={false} />
		{/snippet}
		{#snippet children(studio)}
			{#if studio.forbidden}
				{@render notAuthorised()}
			{:else if studio.error}
				<LoadErrorBanner message={studio.error} onRetry={() => invalidateAll()} />
			{:else}
				<BrandingEditor
					name={studio.name}
					client={studio.client as BrandingEditorValue}
					platform={studio.platform as BrandingEditorValue}
					classifications={studio.classifications}
					groups={studio.groups}
					productTypes={studio.productTypes}
					productTypesUnavailable={studio.productTypesUnavailable}
					canManage={data.canManage}
					canEditPages={data.canEditPages}
					{saving}
					{uploading}
					{error}
					{saved}
					onsave={save}
					onupload={upload}
					onSaveClassification={saveClassification}
					onDeleteClassification={deleteClassification}
					onSaveGroup={saveGroup}
					onDeleteGroup={deleteGroup}
				/>
			{/if}
		{/snippet}
	</Streamed>
{/if}
