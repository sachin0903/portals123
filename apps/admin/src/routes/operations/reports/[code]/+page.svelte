<script lang="ts">
	import { PageHeader } from '@graphene/ui';
	import ReportSwitch from '../report-switch.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const report = $derived(data.report);
</script>

<svelte:head><title>{report.name} · Reports</title></svelte:head>

<PageHeader
	title={report.name}
	description={report.description}
	back={{ href: '/operations/reports', label: 'Reports' }}
>
	{#snippet actions()}
		{#if data.canManage}
			<span class="text-muted-foreground text-sm"
				>{report.enabled ? 'On for every firm' : 'Off'}</span
			>
			<ReportSwitch enabled={report.enabled} label={report.enabled ? 'Switch off' : 'Switch on'} />
		{/if}
	{/snippet}
</PageHeader>

{#if form?.error}
	<p class="text-destructive mt-4 text-sm">{form.error}</p>
{/if}

<section class="mt-8 space-y-6">
	<div>
		<h2 class="text-sm font-medium">Columns</h2>
		<ul class="mt-2 space-y-1 text-sm">
			{#each report.columns as column (column.key)}
				<li>
					<span class="font-medium">{column.label}</span>
					<span class="text-muted-foreground"> · {column.kind}</span>
				</li>
			{/each}
		</ul>
	</div>
	{#if report.params.length > 0}
		<div>
			<h2 class="text-sm font-medium">Parameters</h2>
			<ul class="mt-2 space-y-1 text-sm">
				{#each report.params as param (param.key)}
					<li>
						<span class="font-medium">{param.label}</span>
						<span class="text-muted-foreground">
							· {param.kind}{param.required ? ', required' : ''}
						</span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</section>
