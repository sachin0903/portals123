<script lang="ts">
	import { Button, Card, EmptyState, LoadErrorBanner, PageHeader } from '@graphene/ui';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<PageHeader title="Datasets" description="Data sources document templates resolve at render time."
	>{#snippet actions()}{#if data.canManage}<Button
				href={`/tenants/${data.tenantUid}/operate/datasets/new`}>New dataset</Button
			>{/if}{/snippet}</PageHeader
>{#if data.loadError}<LoadErrorBanner />{/if}<Card glass class="divide-y py-0"
	>{#each data.datasets as ds}<a
			class="hover:bg-muted/20 block px-4 py-3"
			href={`/tenants/${data.tenantUid}/operate/datasets/${ds.uid}`}
			><strong>{ds.name}</strong>
			<div class="text-xs">
				ID: <span class="text-muted-foreground font-mono">{ds.code}</span>
				<span class="text-muted-foreground"
					>· {ds.format} · {ds.enabled ? 'enabled' : 'disabled'}</span
				>
			</div></a
		>{:else}<EmptyState
			title="No datasets yet"
			description="Datasets provide reference content for templates, such as fund lists, fees and disclosures."
		/>{/each}</Card
>
