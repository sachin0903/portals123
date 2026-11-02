<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		Card,
		CardContent,
		humanise,
		PageHeader,
		StatusBadge,
		withToast
	} from '@graphene/ui';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Document request · {data.tenant.name}</title></svelte:head>
<div class="space-y-5">
	<PageHeader
		title={data.request.contact_name ?? 'Document request'}
		back={{
			href: `/tenants/${data.tenant.uid}/operate/documents/requests`,
			label: 'Requests'
		}}
	>
		{#snippet actions()}
			<StatusBadge tone={data.request.status === 'pending' ? 'warning' : 'neutral'}>
				{humanise(data.request.status)}
			</StatusBadge>
		{/snippet}
	</PageHeader>
	{#each data.request.items as item (item.uid)}
		<Card glass>
			<CardContent>
				<p class="font-medium">{item.title}</p>
				<p class="text-muted-foreground text-sm">
					{humanise(item.action)} · {humanise(item.status)}
				</p>
			</CardContent>
		</Card>
	{/each}
	{#if data.writing && data.request.status === 'pending'}
		<form method="POST" action="?/cancel" use:enhance={withToast('Request cancelled')}>
			<Button type="submit" variant="destructive">Cancel request</Button>
		</form>
	{/if}
</div>
