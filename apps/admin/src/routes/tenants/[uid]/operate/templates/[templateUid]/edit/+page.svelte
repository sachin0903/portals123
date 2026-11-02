<script lang="ts">
	import { Button } from '@graphene/ui';
	import { page } from '$app/state';
	import { EmptyState, toast } from '@graphene/ui';
	import { TemplateEditor } from '@graphene/ui/template-authoring';
	import { beforeNavigate, goto } from '$app/navigation';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	let latest: unknown = null;
	async function save(blob: unknown) {
		latest = blob;
		const b = new FormData();
		b.set('schema', JSON.stringify(blob));
		const r = await fetch('?/saveVersion', { method: 'POST', body: b });
		if (!r.ok) throw new Error('save failed');
		if (latest === blob) latest = null;
	}
	async function version(vid: string) {
		const r = await fetch(`versions/${vid}`);
		if (!r.ok) {
			toast.error('Could not load that version.');
			return null;
		}
		return (await r.json()).schema ?? null;
	}
	async function preview(schema: unknown) {
		const r = await fetch('preview', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ schema })
		});
		if (!r.ok) throw new Error('Preview failed');
		return r.json();
	}
	async function previewPdf(schema: unknown) {
		const r = await fetch('preview.pdf', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ schema })
		});
		if (!r.ok) throw new Error('Preview failed');
		return r.blob();
	}
	async function dataset(uid: string, contactId: number | null) {
		const q = new URLSearchParams({ uid });
		if (contactId != null) q.set('contact_id', String(contactId));
		const r = await fetch(`dataset-data?${q}`);
		if (!r.ok) throw new Error('Dataset failed');
		return r.json();
	}
</script>

{#if data.template}<div class="-m-4 md:-m-6">
		<TemplateEditor
			name={data.template.name}
			templateId={data.template.uid}
			documentType={data.template.documentType}
			description={data.template.description}
			backHref={`/tenants/${data.tenantUid}/operate/templates/${data.template.uid}`}
			restore={data.restore}
			formFields={data.formFields}
			versions={data.versions}
			datasetOptions={data.datasetOptions}
			previewClients={data.previewClients}
			brandLogo={data.brandLogo}
			resolvePreviewData={dataset}
			renderPreview={preview}
			renderPreviewPdf={previewPdf}
			onLoadVersion={version}
			onSave={save}
			navigate={goto}
			registerBeforeNavigate={beforeNavigate}
			storageKey={`tpl-editor:${data.tenantUid}:${data.template.uid}`}
			readOnly={!data.canManage}
		/>
	</div>{:else}<EmptyState
		title="Template not found"
		description="It may have been deleted, or the link may be out of date."
	>
		{#snippet action()}
			<Button variant="outline" size="sm" href={`/tenants/${page.params.uid}/operate/templates`}
				>Back to templates</Button
			>
		{/snippet}
	</EmptyState>{/if}
