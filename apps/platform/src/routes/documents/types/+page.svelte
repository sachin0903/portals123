<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { Card, CardContent, LoadErrorBanner, Skeleton, Streamed } from '@graphene/ui';
	import type { PageData, ActionData } from './$types';
	import type { DocumentType } from './+page.server';
	import TypeSection from './type-section.svelte';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// One registry, two vocabularies: document classifications and supporting-file types.
	const byKind = (types: DocumentType[], kind: DocumentType['kind']) =>
		types.filter((t) => t.kind === kind);
</script>

<svelte:head><title>Types · Platform</title></svelte:head>

<div class="space-y-8 pb-16">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Types</h1>
		<p class="text-muted-foreground mt-1">
			The vocabularies your team classifies against. Staff pick from these when uploading; disabled
			types stay on existing items but drop out of the pickers.
		</p>
	</div>

	{#if !data.authorised || !data.registry}
		<Card>
			<CardContent class="py-10 text-center">
				<p class="text-muted-foreground">
					You need the “View Types” permission to see the firm's document-type taxonomy.
				</p>
			</CardContent>
		</Card>
	{:else}
		{#if form?.error}
			<p class="text-destructive text-xs" role="alert">{form.error}</p>
		{/if}

		<!-- The registry is STREAMED (`+page.server.ts`): the heading above is on screen the instant
		     the page is navigated to and the rows land behind it. `keep`, because every later promise
		     is a save re-running the load, and each section's draft and open/closed state live in the
		     component: holding it mounted while the answer is re-read is what lets them survive. -->
		<Streamed data={data.registry} onretry={() => invalidateAll()} keep>
			{#snippet pending()}
				<!-- The two sections' box: a heading line each and a card the height of a short list,
				     so the rows arriving do not move the page. One status region for the whole thing. -->
				<div class="space-y-8" role="status" aria-label="Loading">
					{#each ['document', 'file'] as kind (kind)}
						<section class="space-y-4" aria-hidden="true">
							<div class="flex items-start gap-2">
								<Skeleton class="mt-1 size-4 shrink-0" />
								<div class="flex flex-col gap-2">
									<Skeleton class="h-6 w-48" />
									<Skeleton class="h-4 w-80 max-w-full" />
								</div>
							</div>
							<Card>
								<CardContent>
									<div class="flex h-9 items-center gap-3 border-b">
										<Skeleton class="h-3 w-24" />
										<Skeleton class="h-3 w-32" />
										<Skeleton class="ml-auto h-3 w-16" />
									</div>
									{#each [0, 1, 2, 3, 4] as row (row)}
										<div class="flex h-11 items-center gap-3 border-b last:border-b-0">
											<Skeleton class="h-4 w-40" />
											<Skeleton class="h-4 w-32" />
											<Skeleton class="ml-auto h-5 w-9 rounded-full" />
										</div>
									{/each}
								</CardContent>
							</Card>
						</section>
					{/each}
				</div>
			{/snippet}
			{#snippet children(registry)}
				{#if registry.error}
					<LoadErrorBanner message={registry.error} onRetry={() => invalidateAll()} />
				{/if}

				<TypeSection
					kind="document"
					title="Document types"
					description="How generated + uploaded documents are classified (suitability reports, statements, agreements…)."
					types={byKind(registry.types, 'document')}
					labelPlaceholder="Suitability report"
					slugPlaceholder="suitability_report"
					emptyText="No document types yet."
					canManage={data.canManage}
				/>

				<TypeSection
					kind="file"
					title="Supporting file types"
					description="What client-supplied files are filed as (passport, proof of address, national ID…)."
					types={byKind(registry.types, 'file')}
					labelPlaceholder="Proof of address"
					slugPlaceholder="proof_of_address"
					emptyText="No supporting-file types yet."
					canManage={data.canManage}
				/>
			{/snippet}
		</Streamed>
	{/if}
</div>
