<script lang="ts">
	import { beforeNavigate, goto } from '$app/navigation';
	import { Button, toast } from '@graphene/ui';
	import LayoutTemplateIcon from '@lucide/svelte/icons/layout-template';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import { TemplateEditor } from '@graphene/ui/template-authoring';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const template = $derived(data.template);
	const id = $derived(template?.uid ?? '');

	// Persist the editor blob to the API (POST /templates/{uid}/versions upserts the head draft, or
	// rolls a new snapshot version once the draft is >30 min old). The editor debounces autosave and
	// owns the leave-guard (it has the live editor state), so persist just flushes immediately and
	// resolves/REJECTS with the POST — so the editor keeps its 'unsaved' state + surfaces a failure
	// (the original nulled the buffer up-front and resolved regardless, losing work while reporting
	// success).
	let latest: unknown = null;

	async function flush(): Promise<void> {
		if (latest === null) return;
		const pending = latest;
		const body = new FormData();
		body.append('schema', JSON.stringify(pending));
		let res: Response;
		try {
			res = await fetch('?/saveVersion', { method: 'POST', body });
		} catch {
			throw new Error('save request failed'); // keep `latest` so the next flush retries it
		}
		if (!res.ok) throw new Error(`save failed (${res.status})`);
		// Clear only after the POST succeeds, and only if no newer edit arrived while it was in
		// flight (otherwise the newer blob stays buffered for the next flush).
		if (latest === pending) latest = null;
	}

	function persist(blob: unknown): Promise<void> {
		latest = blob;
		return flush();
	}

	// Fetch one version's saved editor blob on demand (the history panel previews older versions).
	async function loadVersion(vid: string): Promise<Record<string, unknown> | null> {
		const res = await fetch(`/templates/${id}/versions/${vid}`);
		if (!res.ok) {
			toast.error('Could not load that version — please try again.');
			return null;
		}
		return ((await res.json()).schema as Record<string, unknown> | null) ?? null;
	}

	// Resolve a dataset's real rows for the chart-config preview (same snapshot the worker renders).
	async function resolvePreviewData(
		uid: string,
		contactId: number | null
	): Promise<{ columns: string[]; rows: unknown[][] }> {
		const qs = new URLSearchParams({ uid });
		if (contactId != null) qs.set('contact_id', String(contactId));
		const res = await fetch(`/templates/${id}/edit/dataset-data?${qs}`);
		if (!res.ok) {
			// Carry the endpoint's real reason (it now forwards the API detail) to the editor's catch.
			const body = (await res.json().catch(() => null)) as { message?: string } | null;
			throw new Error(body?.message ?? `Resolve failed (${res.status})`);
		}
		return res.json();
	}

	// Render the current editor schema to a data-filled sample document for the Preview mode.
	async function renderPreview(schema: unknown): Promise<{
		html: string | null;
		unknownVariables?: { name: string; suggestion?: string | null }[];
	}> {
		const res = await fetch(`/templates/${id}/edit/preview`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ schema })
		});
		if (!res.ok) throw new Error(`Preview failed (${res.status})`);
		return res.json();
	}

	// Render the current schema to a real PDF with the generation engine (WeasyPrint) — the true
	// indicator of the generated document (pagination, overflow, page breaks). Slower than the HTML
	// preview; the editor shows it as the preview surface and falls back to renderPreview() on failure.
	async function renderPreviewPdf(schema: unknown): Promise<Blob> {
		const res = await fetch(`/templates/${id}/edit/preview.pdf`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ schema })
		});
		if (!res.ok) throw new Error(`PDF preview failed (${res.status})`);
		return res.blob();
	}
</script>

{#if !template}
	<div class="flex flex-col items-center justify-center gap-3 py-24 text-center">
		<LayoutTemplateIcon class="text-muted-foreground/40 size-10" />
		<p class="font-medium">Template not found</p>
		<Button variant="outline" onclick={() => goto('/templates')}>
			<ArrowLeftIcon class="mr-1.5 size-4" /> Back to templates
		</Button>
	</div>
{:else}
	<!-- Full-bleed (the shell topbar is h-14). Content persists to the API via onSave; the editor
	     restores the current version's saved blob. -->
	<div class="-m-4 md:-m-6">
		<TemplateEditor
			name={template.name}
			templateId={template.uid}
			documentType={template.documentType}
			description={template.description}
			backHref={`/templates/${id}`}
			restore={data.restore}
			formFields={data.formFields}
			versions={data.versions}
			datasetOptions={data.datasetOptions}
			previewClients={data.previewClients}
			brandLogo={data.brandLogo}
			{resolvePreviewData}
			{renderPreview}
			{renderPreviewPdf}
			onLoadVersion={loadVersion}
			onSave={persist}
			navigate={goto}
			registerBeforeNavigate={beforeNavigate}
			storageKey={`tpl-editor:${id}`}
			readOnly={!data.canManage}
		/>
	</div>
{/if}
