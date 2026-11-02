<script lang="ts">
	import {
		Button,
		Card,
		EmptyState,
		humanise,
		Input,
		LoadErrorBanner,
		PageHeader,
		SelectMenu,
		Sheet
	} from '@graphene/ui';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	let open = $state(false);
	let kind = $state('article');
</script>

<PageHeader title="Content" description="News, articles and guides shown in the client portal."
	>{#snippet actions()}{#if data.canManage}<Button onclick={() => (open = true)}>New</Button
			>{/if}{/snippet}</PageHeader
>{#if data.loadError}<LoadErrorBanner />{/if}<Card glass class="divide-y py-0"
	>{#each data.items as item}<a
			class="hover:bg-muted/20 block px-4 py-3"
			href={`/tenants/${data.tenantUid}/operate/content/${item.uid}/edit`}
			><strong>{item.title}</strong>
			<div class="text-muted-foreground text-xs">
				{humanise(item.kind)} · {humanise(item.status)}
			</div></a
		>{:else}<EmptyState
			title="No content yet"
			description="Articles, news items and PDF guides this firm publishes to its clients appear here."
		/>{/each}</Card
><Sheet.Root bind:open
	><Sheet.Content
		><Sheet.Header><Sheet.Title>New content</Sheet.Title></Sheet.Header>
		<form method="POST" action="?/create" class="mt-4 space-y-4">
			<SelectMenu
				name="kind"
				bind:value={kind}
				items={[
					{ value: 'article', label: 'Article' },
					{ value: 'news', label: 'News' },
					{ value: 'guide', label: 'PDF guide' }
				]}
			/><Input name="title" placeholder="Title" required /><Button type="submit">Create</Button>
		</form></Sheet.Content
	></Sheet.Root
>
