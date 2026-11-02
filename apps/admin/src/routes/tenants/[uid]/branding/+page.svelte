<script lang="ts">
	import { PageHeader, BrandingEditor, emailCopyFor, type BrandingEditorValue } from '@graphene/ui';
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
</script>

<PageHeader
	eyebrow={data.tenant.name}
	title="Branding"
	description="Logos and accents on the adviser portal. The client portal is the shopfront."
/>

<BrandingEditor
	name={data.name}
	client={data.client as BrandingEditorValue}
	platform={data.platform as BrandingEditorValue}
	canManage={data.canManage}
	canEditPages={data.canEditPages}
	productTypes={data.productTypes}
	productTypesUnavailable={data.productTypesUnavailable}
	{saving}
	{uploading}
	{error}
	{saved}
	onsave={save}
	onupload={upload}
/>
