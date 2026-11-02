<script lang="ts">
	// The outcome of a generate: status, the filed PDF, and the CTC request that produced it.
	// Shared by the drawer and the full-page composer so the two cannot disagree about what a
	// successful (or placeholder) illustration looks like.
	import { Button, Card, CardContent, PdfViewer, StatusBadge } from '@graphene/ui';
	import CodeIcon from '@lucide/svelte/icons/code-xml';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import { formatXml } from '$lib/components/illustrations/format-xml';
	import {
		describeResult,
		filedDocumentPath,
		type GenerateResult
	} from '$lib/illustrations/generate';
	import type { Snippet } from 'svelte';

	let {
		result,
		contactUid,
		requestOpen = $bindable(false),
		extraActions
	}: {
		result: GenerateResult;
		contactUid: string;
		requestOpen?: boolean;
		extraActions?: Snippet;
	} = $props();

	const summary = $derived(describeResult(result));
	const documentHref = $derived(filedDocumentPath(contactUid, result));
	const pdfSrc = $derived(
		result.stored && result.document_uid ? `/documents/${result.document_uid}/content` : null
	);
</script>

<Card glass>
	<CardContent class="space-y-3">
		<div class="flex flex-wrap items-center gap-2">
			<StatusBadge tone={summary.tone}>{summary.label}</StatusBadge>
			<p class="text-sm font-medium">{summary.headline}</p>
			{#if documentHref}
				<Button variant="ghost" size="sm" href={documentHref}>
					<FileTextIcon class="mr-1.5 size-3.5" /> Open document
				</Button>
			{/if}
			{#if extraActions}{@render extraActions()}{/if}
		</div>

		{#if result.placeholder && result.placeholder_reason}
			<p class="text-muted-foreground text-sm">{result.placeholder_reason}</p>
			<p class="text-sm">
				This PDF carries no projected values and is marked on every page. Do not issue it to a
				client.
			</p>
		{/if}

		{#if result.errors.length}
			<ul class="text-destructive list-inside list-disc text-sm">
				{#each result.errors as e (e)}<li>{e}</li>{/each}
			</ul>
		{/if}

		{#if pdfSrc}
			<div class="bg-muted/30 h-[min(36rem,70vh)] min-h-80 overflow-hidden rounded-md border">
				<PdfViewer src={pdfSrc} class="h-full w-full" />
			</div>
		{/if}

		{#if result.request_xml}
			<div class="space-y-2">
				<Button variant="outline" size="sm" onclick={() => (requestOpen = !requestOpen)}>
					<CodeIcon class="mr-1.5 size-3.5" />
					{requestOpen ? 'Hide' : 'Show'} calculation details
				</Button>
				{#if requestOpen}
					<pre
						class="bg-muted/50 max-h-80 overflow-auto rounded-md p-3 font-mono text-xs leading-relaxed">{formatXml(
							result.request_xml
						)}</pre>
					<p class="text-muted-foreground text-xs">
						Technical calculation details supplied to Morgan Lloyd.
					</p>
				{/if}
			</div>
		{/if}
	</CardContent>
</Card>
