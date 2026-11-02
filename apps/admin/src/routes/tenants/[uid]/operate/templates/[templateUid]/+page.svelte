<script lang="ts">
	import { page } from '$app/state';
	import {
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		EmptyState,
		humanise,
		Input,
		Label,
		LoadErrorBanner,
		PageHeader,
		Textarea,
		toast
	} from '@graphene/ui';
	import { DatasetLinkDialog, FormLinkDialog } from '@graphene/ui/template-authoring';
	import { deserialize } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let formOpen = $state(false);
	let datasetsOpen = $state(false);

	async function post(action: string, body: FormData, success: string) {
		const response = await fetch(action, { method: 'POST', body });
		const result = deserialize(await response.text());
		if (result.type === 'success') {
			toast.success(success);
			await invalidateAll();
			return result.data;
		}
		toast.error(
			result.type === 'failure'
				? String(result.data?.error ?? 'That did not work.')
				: 'That did not work.'
		);
		return null;
	}

	function setForm(code: string | null) {
		const body = new FormData();
		body.set('code', code ?? '');
		void post('?/setInputForm', body, code ? 'Input form linked.' : 'Input form removed.');
	}

	function toggleDataset(code: string) {
		if (!data.template) return;
		const linked = data.template.linkedDatasets ?? [];
		const next = linked.includes(code) ? linked.filter((item) => item !== code) : [...linked, code];
		const body = new FormData();
		body.set('codes', JSON.stringify(next));
		void post('?/setDatasets', body, 'Dataset links updated.');
	}

	async function duplicate() {
		const result = await post('?/duplicate', new FormData(), 'Template duplicated.');
		if (result?.uid) await goto(`/tenants/${data.tenantUid}/operate/templates/${result.uid}/edit`);
	}
</script>

{#if data.loadError}<LoadErrorBanner />{/if}
{#if data.template}
	{@const template = data.template}
	{@const latest = data.versions[0]}
	<PageHeader title={template.name} description={`${template.code} · ${template.documentType}`}>
		{#snippet actions()}
			<Button
				variant="outline"
				href={`/tenants/${data.tenantUid}/operate/templates/${template.uid}/edit`}
			>
				{data.canManage ? 'Edit design' : 'View design'}
			</Button>
			{#if data.canManage && latest?.status === 'draft' && !template.archivedAt}
				<form method="POST" action="?/publish">
					<input type="hidden" name="vid" value={latest.uid} />
					<Button type="submit">Publish</Button>
				</form>
			{/if}
			{#if data.canManage}
				<form method="POST" action={template.archivedAt ? '?/unarchive' : '?/archive'}>
					<Button type="submit" variant="ghost"
						>{template.archivedAt ? 'Restore' : 'Archive'}</Button
					>
				</form>
				<Button variant="ghost" onclick={duplicate}>Duplicate</Button>
			{/if}
		{/snippet}
	</PageHeader>

	<div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
		<div class="space-y-4">
			<Card glass>
				<CardHeader><CardTitle>Preview</CardTitle></CardHeader>
				<CardContent>
					<iframe
						title={`Preview of ${template.name}`}
						src={`/tenants/${data.tenantUid}/operate/templates/${template.uid}/preview.pdf`}
						class="bg-background h-[38rem] w-full rounded-lg border"
					></iframe>
				</CardContent>
			</Card>
			<Card glass>
				<CardHeader><CardTitle>Version history</CardTitle></CardHeader>
				<CardContent class="divide-y">
					{#each data.versions as version (version.uid)}
						<div class="flex items-center justify-between py-3">
							<span>Version {version.versionNumber}</span>
							<span class="text-muted-foreground text-sm">{humanise(version.status)}</span>
						</div>
					{:else}
						<p class="text-muted-foreground text-sm">No versions saved yet.</p>
					{/each}
				</CardContent>
			</Card>
		</div>

		<div class="space-y-4">
			<Card glass>
				<CardHeader><CardTitle>Details</CardTitle></CardHeader>
				<CardContent>
					<form method="POST" action="?/updateDetails" class="space-y-3">
						<div class="space-y-1">
							<Label for="name">Name</Label><Input
								id="name"
								name="name"
								value={template.name}
								disabled={!data.canManage}
							/>
						</div>
						<div class="space-y-1">
							<Label for="description">Description</Label><Textarea
								id="description"
								name="description"
								value={template.description ?? ''}
								disabled={!data.canManage}
							/>
						</div>
						<input type="hidden" name="document_type" value={template.documentType} />
						{#if data.canManage}<Button type="submit" class="w-full">Save details</Button>{/if}
					</form>
				</CardContent>
			</Card>
			<Card glass>
				<CardHeader><CardTitle>Template data</CardTitle></CardHeader>
				<CardContent class="space-y-3">
					<p class="text-muted-foreground text-sm">
						Input form: {template.inputFormCode ?? 'None'}<br />
						Datasets: {(template.linkedDatasets ?? []).join(', ') || 'None'}
					</p>
					{#if data.canManage}
						<Button variant="outline" class="w-full" onclick={() => (formOpen = true)}
							>Choose input form</Button
						>
						<Button variant="outline" class="w-full" onclick={() => (datasetsOpen = true)}
							>Link datasets</Button
						>
					{/if}
				</CardContent>
			</Card>
		</div>
	</div>

	<FormLinkDialog
		bind:open={formOpen}
		forms={data.forms}
		documentType={template.documentType}
		value={template.inputFormCode ?? null}
		onsave={setForm}
	/>
	<DatasetLinkDialog
		bind:open={datasetsOpen}
		datasets={data.datasets}
		linked={template.linkedDatasets ?? []}
		ontoggle={toggleDataset}
	/>
{:else}
	<EmptyState
		title="Template not found"
		description="It may have been deleted, or the link may be out of date."
	>
		{#snippet action()}
			<Button variant="outline" size="sm" href={`/tenants/${page.params.uid}/operate/templates`}
				>Back to templates</Button
			>
		{/snippet}
	</EmptyState>
{/if}
