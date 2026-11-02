<script lang="ts">
	import { page } from '$app/state';
	import {
		Button,
		Card,
		EmptyState,
		Input,
		LoadErrorBanner,
		PageHeader,
		Switch,
		Textarea
	} from '@graphene/ui';
	import { DatasetDataGrid, toCsvLine } from '@graphene/ui/dataset-authoring';
	import { untrack } from 'svelte';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const initialSample = untrack(() => data.sample);
	let columns = $state(initialSample?.columns ?? ['Column 1']),
		rows = $state((initialSample?.rows ?? [['']]).map((r) => r.map(String)));
	const serialisedRows = $derived(rows.map(toCsvLine).join('\n'));
</script>

{#if data.loadError}<LoadErrorBanner />{/if}{#if data.dataset}<PageHeader
		title={data.dataset.name}
		description={`${data.dataset.code} · ${data.dataset.format}`}
	/>
	<div class="grid gap-4 lg:grid-cols-[1fr_20rem]">
		<Card glass
			>{#if data.sample}<DatasetDataGrid bind:columns bind:rows />{:else}<EmptyState
					title="No stored rows"
					description="This dataset version is empty. Templates using it will leave those fields blank."
				/>{/if}{#if data.canManage}<form method="POST" action="?/addVersion">
					<input type="hidden" name="format" value={data.dataset.format} /><input
						type="hidden"
						name="columns"
						value={toCsvLine(columns)}
					/><input
						type="hidden"
						name="rows"
						value={serialisedRows}
					/>{#if data.dataset.format === 'sql'}<Textarea
							name="sql_query"
							value={data.versions[0]?.sqlQuery ?? ''}
						/>{/if}<Button type="submit">Publish new version</Button>
				</form>{/if}</Card
		><Card glass
			>{#if data.canManage}<form method="POST" action="?/updateDetails" class="space-y-3">
					<Input name="name" value={data.dataset.name} /><Textarea
						name="description"
						value={data.dataset.description ?? ''}
					/><label class="flex items-center justify-between gap-3 text-sm"
						><span>Enabled</span><Switch
							name="enabled"
							checked={data.dataset.enabled}
							disabled={!data.canManage}
						/></label
					><Button type="submit">Save details</Button>
				</form>{:else}<div class="space-y-2 text-sm">
					<p><strong>Name:</strong> {data.dataset.name}</p>
					<p><strong>Description:</strong> {data.dataset.description ?? '—'}</p>
					<p><strong>Enabled:</strong> {data.dataset.enabled ? 'Yes' : 'No'}</p>
				</div>{/if}
			<h2 class="mt-5 font-semibold">Versions</h2>
			{#each data.versions as v}<p>v{v.versionNumber}</p>{/each}</Card
		>
	</div>{:else}<EmptyState
		title="Dataset not found"
		description="It may have been deleted, or the link may be out of date."
	>
		{#snippet action()}
			<Button variant="outline" size="sm" href={`/tenants/${page.params.uid}/operate/datasets`}
				>Back to datasets</Button
			>
		{/snippet}
	</EmptyState>{/if}
