<script lang="ts">
	import {
		Button,
		Card,
		CreateTemplateDialog,
		EmptyState,
		LoadErrorBanner,
		PageHeader,
		type NewTemplateInput
	} from '@graphene/ui';
	import { goto, invalidateAll } from '$app/navigation';
	import { deserialize } from '$app/forms';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	let createOpen = $state(false);
	async function create(input: NewTemplateInput) {
		const b = new FormData();
		b.set('input', JSON.stringify(input));
		const r = deserialize(await (await fetch('?/create', { method: 'POST', body: b })).text());
		if (r.type === 'success' && r.data?.uid) {
			await goto(`/tenants/${data.tenantUid}/operate/templates/${r.data.uid}/edit`);
			return { ok: true };
		}
		return {
			ok: false,
			error: r.type === 'failure' ? String(r.data?.error) : 'Could not create template.'
		};
	}
</script>

<PageHeader
	title="Templates"
	description="Create, version and publish the firm's document templates."
	>{#snippet actions()}{#if data.canManage}<Button onclick={() => (createOpen = true)}
				>New template</Button
			>{/if}{/snippet}</PageHeader
>{#if data.loadError}<LoadErrorBanner onRetry={() => invalidateAll()} />{/if}<Card
	glass
	class="divide-y py-0"
	>{#each data.templates as template}<a
			class="hover:bg-muted/20 block px-4 py-3"
			href={`/tenants/${data.tenantUid}/operate/templates/${template.uid}`}
			><strong>{template.name}</strong>
			<div class="text-xs">
				ID: <span class="text-muted-foreground font-mono">{template.code}</span>
				<span class="text-muted-foreground">· {template.currentVersionStatus ?? 'draft'}</span>
			</div></a
		>{:else}<EmptyState
			title="No templates yet"
			description="Templates create documents such as suitability reports, client letters and illustration covers."
		/>{/each}</Card
><CreateTemplateDialog
	bind:open={createOpen}
	datasets={data.datasets}
	forms={data.forms}
	existingCodes={data.templates.map((t) => t.code)}
	oncreate={create}
/>
