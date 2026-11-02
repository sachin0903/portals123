<script lang="ts">
	import { page } from '$app/state';
	import { ArticleEditor, DEFAULT_ARTICLE_CSS } from '@graphene/ui/content-editor';
	import {
		Button,
		EmptyState,
		FileDropzone,
		Input,
		LoadErrorBanner,
		PageHeader,
		Textarea
	} from '@graphene/ui';
	import { untrack } from 'svelte';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const initial = untrack(() => data.item);
	let html = $state(initial?.schema?.html ?? '');
	let css = $state(initial?.schema?.css ?? DEFAULT_ARTICLE_CSS);
</script>

{#if data.loadError}<LoadErrorBanner />{/if}{#if data.item}<PageHeader
		title={data.item.title}
		description={data.item.status}
		>{#snippet actions()}{#if data.canManage}<form method="POST" action="?/publish">
					<Button type="submit">Publish</Button>
				</form>
				<form method="POST" action="?/archive">
					<Button type="submit" variant="ghost">Archive</Button>
				</form>{/if}{/snippet}</PageHeader
	>
	{#if data.item.kind === 'guide' && data.canManage}
		<form
			method="POST"
			action="guide"
			enctype="multipart/form-data"
			class="mb-5 max-w-xl space-y-3"
		>
			<FileDropzone
				name="file"
				accept="application/pdf"
				title="Upload guide PDF"
				formats="PDF"
				rich
			/>
			<Button type="submit" variant="outline">Upload guide</Button>
		</form>
	{/if}
	<form method="POST" action="?/save" class="grid gap-5 xl:grid-cols-[1fr_20rem]">
		<div>
			{#if data.item.kind === 'guide'}<a class="underline" href="guide-file">Open uploaded guide</a
				>{:else}<ArticleEditor
					{html}
					onchange={(v) => {
						html = v.html;
						css = v.css;
					}}
					onUpload={async (file) => {
						const b = new FormData();
						b.set('file', file);
						const r = await fetch('images', { method: 'POST', body: b });
						if (!r.ok) throw new Error('Upload failed');
						return (await r.json()).src;
					}}
				/><input type="hidden" name="html" value={html} /><input
					type="hidden"
					name="css"
					value={css}
				/>{/if}
		</div>
		<aside class="space-y-3">
			<Input name="title" value={data.item.title} /><Textarea
				name="summary"
				value={data.item.summary ?? ''}
			/>{#if data.canManage}<Button type="submit">Save draft</Button>{/if}
		</aside>
	</form>{:else}<EmptyState
		title="Content not found"
		description="It may have been deleted, or the link may be out of date."
	>
		{#snippet action()}
			<Button variant="outline" size="sm" href={`/tenants/${page.params.uid}/operate/content`}
				>Back to content</Button
			>
		{/snippet}
	</EmptyState>{/if}
