<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Button,
		EmptyState,
		Input,
		Label,
		PageHeader,
		SelectMenu,
		Textarea
	} from '@graphene/ui';
	import { page } from '$app/state';
	import { DatasetDataGrid, toCsvLine } from '@graphene/ui/dataset-authoring';
	import type { ActionData, PageData } from './$types';
	let { data, form }: { data: PageData; form: ActionData } = $props();
	let format = $state('inline'),
		columns = $state(['Column 1', 'Column 2']),
		rows = $state([['', '']]);
	const serialisedRows = $derived(rows.map(toCsvLine).join('\n'));
</script>

<PageHeader
	title="New dataset"
	description="A data source this firm’s templates can draw on at render time."
/>
{#if !data.canManage}
	<EmptyState
		title="Read-only workspace"
		description="Creating a dataset for a firm takes write access to their workspace."
	>
		{#snippet action()}
			<Button variant="outline" href={`/tenants/${page.params.uid}/operate/datasets`}>
				Back to datasets
			</Button>
		{/snippet}
	</EmptyState>
{:else}
	<form method="POST" class="mx-auto max-w-2xl space-y-4">
		{#if form?.error}<Alert variant="destructive"
				><AlertDescription>{form.error}</AlertDescription></Alert
			>{/if}<Label>Name</Label><Input name="name" required /><Label>Code</Label><Input
			name="code"
			required
		/><Label>Description</Label><Textarea name="description" /><Label>Format</Label><SelectMenu
			name="format"
			bind:value={format}
			items={[
				{ value: 'inline', label: 'Table' },
				{ value: 'sql', label: 'SQL' }
			]}
		/><input
			type="hidden"
			name="scope"
			value={format === 'sql' ? 'subject' : 'global'}
		/>{#if format === 'sql'}<Textarea
				name="sql_query"
				placeholder="SELECT …"
			/>{:else}<DatasetDataGrid bind:columns bind:rows /><input
				type="hidden"
				name="columns"
				value={toCsvLine(columns)}
			/><input type="hidden" name="rows" value={serialisedRows} />{/if}<Button type="submit"
			>Create dataset</Button
		>
	</form>
{/if}
