<script lang="ts">
	import { Badge, Button } from '@graphene/ui';
	import { highlightSql, MAT_GLYPH, STATE_LABEL, STATE_VAR, stateBadgeVariant } from '../status';
	import type { PlatformStore } from '../store.svelte';

	let { store }: { store: PlatformStore } = $props();

	const model = $derived(store.selectedModel);
	const downstream = $derived(model ? (store.downstream.get(model.name) ?? []) : []);

	// Wide relations (raw CRM mirrors) carry hundreds of columns — show the first
	// few and expand on demand. Resets when the selection changes.
	const COLS_SHOWN = 10;
	let allCols = $state(false);
	$effect(() => {
		void model?.name;
		allCols = false;
	});
	const cols = $derived(model ? (allCols ? model.cols : model.cols.slice(0, COLS_SHOWN)) : []);
</script>

{#if model}
	<div class="space-y-4">
		<div>
			<div class="text-muted-foreground text-[11px] font-medium">Model</div>
			<h2 class="font-mono text-lg font-semibold break-all">{model.name}</h2>
		</div>

		<div class="flex flex-wrap items-center gap-1.5">
			{#if model.running}
				<Badge variant="secondary" class="gap-1.5">
					<span class="size-1.5 rounded-full" style="background: var(--ring)"></span>running
				</Badge>
			{:else}
				<Badge variant={stateBadgeVariant(model.state)} class="gap-1.5">
					<span class="size-1.5 rounded-full" style="background: var({STATE_VAR[model.state]})"
					></span>
					{STATE_LABEL[model.state]}
				</Badge>
			{/if}
			<Badge variant="outline" class="font-mono">{MAT_GLYPH[model.mat]} {model.mat}</Badge>
			{#if model.strategy !== '—'}
				<Badge variant="outline" class="font-mono">{model.strategy}</Badge>
			{/if}
		</div>

		{#if model.description}
			<p class="text-muted-foreground text-sm">{model.description}</p>
		{/if}

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

		<div>
			<div class="text-muted-foreground mb-1.5 text-[11px] font-medium">Column lineage</div>
			<div class="space-y-0.5 font-mono text-xs">
				{#each cols as col (col.out)}
					<div class="flex items-baseline gap-2">
						<span class="min-w-24 font-medium">{col.out}</span>
						<span class="text-muted-foreground">←</span>
						{#if col.srcCol === '—'}
							<span class="text-muted-foreground italic">literal / external</span>
						{:else}
							<span class="text-muted-foreground break-all">{col.srcModel}.{col.srcCol}</span>
						{/if}
					</div>
				{/each}
			</div>
			{#if model.cols.length > COLS_SHOWN}
				<Button
					variant="ghost"
					size="sm"
					class="text-muted-foreground mt-1 h-6 px-1.5 text-xs"
					onclick={() => (allCols = !allCols)}
				>
					{allCols ? 'Show fewer' : `Show all ${model.cols.length} columns`}
				</Button>
			{/if}
		</div>

		<div>
			<div class="text-muted-foreground mb-1.5 text-[11px] font-medium">Definition</div>
			<!-- eslint-disable svelte/no-at-html-tags -- highlightSql escapes its input before adding markup spans -->
			<pre
				class="bg-muted/50 overflow-x-auto rounded-lg border p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">{@html highlightSql(
					model.sql
				)}</pre>
			<!-- eslint-enable svelte/no-at-html-tags -->
		</div>
	</div>
{/if}
