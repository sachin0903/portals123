<script lang="ts">
	import { Badge, Button, Card, CardContent, Separator } from '@graphene/ui';
	import type { PlatformStore } from '../store.svelte';
	import EventFeed from './EventFeed.svelte';
	import LineageCanvas from './LineageCanvas.svelte';
	import ModelDetail from './ModelDetail.svelte';

	let {
		store,
		/** Height for the canvas and the detail rail beside it — they stay the same height so the
		 *  row doesn't look ragged. Defaults to the fixed height the tabs view used. */
		heightClass = 'h-[560px]'
	}: { store: PlatformStore; heightClass?: string } = $props();

	const counts = $derived(store.planCounts);
	let query = $state('');
	const hits = $derived(
		query.trim()
			? store.data.models
					.filter((m) => m.name.toLowerCase().includes(query.trim().toLowerCase()))
					.slice(0, 8)
			: []
	);

	function pick(name: string) {
		store.focus(name);
		query = name;
	}
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center gap-2">
		<span class="text-muted-foreground text-sm">
			{store.data.models.length} models
		</span>
		{#if counts.breaking}
			<Badge variant="destructive">{counts.breaking} rebuild</Badge>
		{/if}
		{#if counts.nonbreaking}
			<Badge variant="secondary" class="gap-1.5">
				<span class="size-1.5 rounded-full" style="background: var(--brand)"></span>
				{counts.nonbreaking} refresh
			</Badge>
		{/if}
		{#if counts.added}
			<Badge variant="secondary" class="gap-1.5">
				<span class="size-1.5 rounded-full" style="background: var(--positive)"></span>
				{counts.added} added
			</Badge>
		{/if}
		<span class="text-muted-foreground text-xs">
			rebuild / refresh is the compiled plan for the next run, not a pending apply
		</span>
		<div class="relative ml-auto flex items-center gap-2">
			<input
				bind:value={query}
				placeholder="Focus a model…"
				aria-label="Focus a model"
				class="bg-card focus-visible:ring-ring/50 h-8 w-52 rounded-lg border px-2 font-mono text-xs outline-none focus-visible:ring-[3px]"
			/>
			{#if hits.length && query.trim() && query !== store.selected}
				<ul
					class="bg-popover absolute top-9 right-0 z-10 w-72 overflow-hidden rounded-lg border shadow-md"
				>
					{#each hits as model (model.name)}
						<li>
							<button
								type="button"
								class="hover:bg-accent flex w-full items-center gap-2 px-2 py-1.5 text-left font-mono text-xs"
								onmousedown={(e) => {
									e.preventDefault();
									pick(model.name);
								}}
							>
								<span class="min-w-0 flex-1 truncate">{model.name}</span>
								<span class="text-muted-foreground shrink-0">{model.mat}</span>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
			<Button size="sm" variant="outline" onclick={() => store.expandAll()}>Expand columns</Button>
			<Button size="sm" variant="outline" onclick={() => store.collapseAll()}>Collapse</Button>
		</div>
	</div>

	<div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
		<LineageCanvas {store} {heightClass} />
		<Card class={`overflow-hidden ${heightClass}`}>
			<CardContent class="flex h-full min-h-0 flex-col gap-3 overflow-hidden">
				<div class="min-h-0 flex-1 overflow-y-auto pr-1">
					<ModelDetail {store} />
				</div>
				<Separator />
				<div class="h-44 shrink-0">
					<EventFeed script={store.data.feedScript} />
				</div>
			</CardContent>
		</Card>
	</div>
</div>
