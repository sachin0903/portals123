<script lang="ts">
	// A model's detail, in a side drawer off the models list.
	//
	// Three views, defaulting to DATA: what a relation actually contains is the first question
	// asked of it, and it's the one the old inline panel answered last. Schema is separated from
	// Definition because they come from different places and disagree in a useful way — the
	// warehouse `DESCRIBE` is truth for Python-extracted relations, which have no SQL and no
	// column lineage at all. That's why Python models looked like they had no schema: the panel
	// only ever showed lineage.
	import { Badge, Button, Segmented, Sheet, Table } from '@graphene/ui';
	import WaypointsIcon from '@lucide/svelte/icons/waypoints';
	import { MAT_GLYPH, STATE_LABEL, STATE_VAR, highlightSql, stateBadgeVariant } from '../status';
	import type { PlatformStore } from '../store.svelte';
	import ResultGrid from './ResultGrid.svelte';

	let { open = $bindable(false), store }: { open?: boolean; store: PlatformStore } = $props();

	let view = $state('data');

	const model = $derived(store.selectedModel);
	const live = $derived(store.data.live === true);
	const relation = $derived(model?.relation ?? model?.name ?? '');
	const downstream = $derived(model ? (store.downstream.get(model.name) ?? []) : []);
	// A Python extractor has no SQL; the daemon sends none and the mapper substitutes a note.
	const isPython = $derived(!!model?.sql?.startsWith('-- Python model'));

	let livePreview = $state<{ columns: string[]; rows: (string | number | boolean)[][] } | null>(
		null
	);
	let previewError = $state<string | null>(null);
	let previewLoading = $state(false);
	let previewedRelation = $state<string | null>(null);

	$effect(() => {
		if (!open || !live || view !== 'data' || !model) return;
		// Don't refetch the same relation when the drawer re-renders for an unrelated reason.
		if (previewedRelation === relation) return;
		previewedRelation = relation;
		previewLoading = true;
		previewError = null;
		fetch(`/platforms/${store.data.meta.provider}/query/run`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ sql: `select * from ${relation} limit 50` })
		})
			.then(async (res) => {
				const body = await res.json();
				if (!res.ok) throw new Error(String(body?.detail ?? `preview failed (${res.status})`));
				livePreview = body;
			})
			.catch((e: unknown) => {
				livePreview = null;
				previewError = e instanceof Error ? e.message : 'Preview failed.';
			})
			.finally(() => (previewLoading = false));
	});

	// Reset to Data when a different model is opened — the previous view rarely carries over.
	$effect(() => {
		void model?.name;
		view = 'data';
		previewedRelation = null;
	});
</script>

<Sheet.Root bind:open>
	<Sheet.Content class="flex w-full flex-col gap-0 p-0 sm:!w-[62vw] sm:!max-w-none">
		{#if model}
			<Sheet.Header class="border-b px-6 pt-6 pb-4">
				<Sheet.Title class="font-mono break-all">{model.name}</Sheet.Title>
				<Sheet.Description>
					<span class="flex flex-wrap items-center gap-1.5">
						{#if model.running}
							<Badge variant="secondary" class="gap-1.5">
								<span class="size-1.5 rounded-full" style="background: var(--ring)"></span>running
							</Badge>
						{:else}
							<Badge variant={stateBadgeVariant(model.state)} class="gap-1.5">
								<span
									class="size-1.5 rounded-full"
									style="background: var({STATE_VAR[model.state]})"
								></span>
								{STATE_LABEL[model.state]}
							</Badge>
						{/if}
						<Badge variant="outline" class="font-mono">{MAT_GLYPH[model.mat]} {model.mat}</Badge>
						{#if model.strategy !== '—'}
							<Badge variant="outline" class="font-mono">{model.strategy}</Badge>
						{/if}
						{#if isPython}
							<Badge variant="outline">Python extractor</Badge>
						{/if}
						<span class="text-muted-foreground font-mono text-xs">{relation}</span>
					</span>
				</Sheet.Description>
			</Sheet.Header>

			<div class="flex items-center gap-2 border-b px-6 py-3">
				<Segmented
					options={[
						{ value: 'data', label: 'Data' },
						{ value: 'schema', label: 'Columns' },
						{ value: 'definition', label: 'Definition' }
					]}
					bind:value={view}
				/>
				<Button
					size="sm"
					variant="ghost"
					class="ml-auto"
					href={`/platforms/${store.data.meta.provider}/lineage`}
				>
					<WaypointsIcon class="size-4" /> View in lineage
				</Button>
			</div>

			<div class="min-h-0 flex-1 overflow-y-auto px-6 py-4">
				{#if view === 'data'}
					{#if live}
						<div class="space-y-2">
							<div class="text-muted-foreground text-xs">
								{#if previewLoading}
									loading…
								{:else if livePreview}
									{livePreview.rows.length} of {model.rows} rows · live warehouse data
								{/if}
							</div>
							{#if previewError}
								<div class="text-destructive font-mono text-xs">{previewError}</div>
							{:else if livePreview}
								<ResultGrid columns={livePreview.columns} rows={livePreview.rows} />
							{/if}
						</div>
					{/if}
				{:else if view === 'schema'}
					<!-- Straight from the warehouse DESCRIBE, so it works for Python models too. -->
					{#if model.schema?.length}
						<Table.Root>
							<Table.Header>
								<Table.Row>
									<Table.Head>Column</Table.Head>
									<Table.Head>Type</Table.Head>
									<Table.Head>Derived from</Table.Head>
								</Table.Row>
							</Table.Header>
							<Table.Body>
								{#each model.schema as col (col.name)}
									{@const lineage = model.cols.find((c) => c.out === col.name)}
									<Table.Row>
										<Table.Cell class="font-mono text-xs font-medium">{col.name}</Table.Cell>
										<Table.Cell class="text-muted-foreground font-mono text-xs">
											{col.type}
										</Table.Cell>
										<Table.Cell class="text-muted-foreground font-mono text-xs">
											{#if lineage && lineage.srcCol !== '—'}
												{lineage.srcModel}.{lineage.srcCol}
											{:else}
												<span class="italic">—</span>
											{/if}
										</Table.Cell>
									</Table.Row>
								{/each}
							</Table.Body>
						</Table.Root>
					{:else}
						<p class="text-muted-foreground py-8 text-center text-sm">
							Column details are not available yet.
						</p>
					{/if}
				{:else}
					<div class="space-y-4">
						<dl class="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 font-mono text-xs">
							<dt class="text-muted-foreground">fingerprint</dt>
							<dd>{model.fingerprint}</dd>
							<dt class="text-muted-foreground">rows</dt>
							<dd>{model.rows}</dd>
							<dt class="text-muted-foreground">schedule</dt>
							<dd>{model.schedule}</dd>
							<dt class="text-muted-foreground">owner</dt>
							<dd>{model.owner}</dd>
							<dt class="text-muted-foreground">upstream</dt>
							<dd class="break-all">{model.deps.join(', ') || '—'}</dd>
							<dt class="text-muted-foreground">downstream</dt>
							<dd class="break-all">{downstream.join(', ') || '—'}</dd>
						</dl>
						{#if model.description}
							<p class="text-muted-foreground text-sm">{model.description}</p>
						{/if}
						<!-- eslint-disable svelte/no-at-html-tags -- highlightSql escapes before adding spans -->
						<pre
							class="bg-muted/50 overflow-x-auto rounded-lg border p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">{@html highlightSql(
								model.sql
							)}</pre>
						<!-- eslint-enable svelte/no-at-html-tags -->
					</div>
				{/if}
			</div>
		{/if}
	</Sheet.Content>
</Sheet.Root>
