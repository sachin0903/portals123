<script lang="ts">
	import {
		Dialog,
		Button,
		Input,
		Checkbox,
		SelectMenu,
		DATASET_FORMAT_META,
		DATASET_SCOPE_META,
		type Dataset,
		type DatasetFormat,
		type DatasetScope
	} from '@graphene/ui';
	import SearchIcon from '@lucide/svelte/icons/search';
	import DatabaseIcon from '@lucide/svelte/icons/database';

	// Larger, filterable multi-select dataset picker. Toggling links/unlinks live via
	// `ontoggle` (the caller owns the linked set); the dialog just reflects `linked`.
	let {
		open = $bindable(false),
		datasets = [],
		linked = [],
		ontoggle
	}: {
		open?: boolean;
		datasets?: Dataset[];
		linked?: string[];
		ontoggle?: (code: string) => void;
	} = $props();

	let search = $state('');
	let formatFilter = $state<'all' | DatasetFormat>('all');
	let scopeFilter = $state<'all' | DatasetScope>('all');

	const FORMATS = Object.entries(DATASET_FORMAT_META) as [DatasetFormat, { label: string }][];
	const SCOPES = Object.entries(DATASET_SCOPE_META) as [DatasetScope, { label: string }][];

	let lastOpen = false;
	$effect(() => {
		if (open && !lastOpen) {
			search = '';
			formatFilter = 'all';
			scopeFilter = 'all';
		}
		lastOpen = open;
	});

	const filtered = $derived(
		datasets.filter((d) => {
			const q = search.trim().toLowerCase();
			if (q && !`${d.name} ${d.code}`.toLowerCase().includes(q)) return false;
			if (formatFilter !== 'all' && d.format !== formatFilter) return false;
			if (scopeFilter !== 'all' && d.scope !== scopeFilter) return false;
			return true;
		})
	);

	const baseOpt = 'flex items-center gap-3 rounded-xl border p-3 text-left transition-all';
	const selOpt = 'border-ring bg-muted/40 ring-ring/30 ring-1';
	const idleOpt = 'hover:border-ring/50 hover:bg-muted/20';
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[85vh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
		<Dialog.Header class="space-y-1 border-b px-6 py-4 text-left">
			<Dialog.Title class="flex items-center gap-2">
				<DatabaseIcon class="text-primary size-4" /> Link datasets
			</Dialog.Title>
			<Dialog.Description>
				The data sources this template pulls in at render time. Toggle any number.
			</Dialog.Description>
		</Dialog.Header>

		<!-- Filter toolbar -->
		<div class="flex flex-wrap items-center gap-2 border-b px-6 py-3">
			<div class="relative min-w-[12rem] flex-1">
				<SearchIcon
					class="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
				/>
				<Input placeholder="Search datasets…" class="pl-9" bind:value={search} />
			</div>
			<SelectMenu
				class="w-40"
				items={[
					{ value: 'all', label: 'All formats' },
					...FORMATS.map(([key, meta]) => ({ value: key, label: meta.label }))
				]}
				bind:value={formatFilter}
			/>
			<SelectMenu
				class="w-40"
				items={[
					{ value: 'all', label: 'All scopes' },
					...SCOPES.map(([key, meta]) => ({ value: key, label: meta.label }))
				]}
				bind:value={scopeFilter}
			/>
		</div>

		<!-- Options -->
		<div class="min-h-[16rem] flex-1 overflow-y-auto px-6 py-4">
			{#if datasets.length === 0}
				<div
					class="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 py-12 text-center text-sm"
				>
					<DatabaseIcon class="size-8 opacity-40" />
					No datasets yet — create one in Datasets first.
				</div>
			{:else}
				<div class="grid gap-2.5 sm:grid-cols-2">
					{#each filtered as ds (ds.uid)}
						{@const sm = DATASET_FORMAT_META[ds.format]}
						{@const scope = DATASET_SCOPE_META[ds.scope]}
						{@const on = linked.includes(ds.code)}
						<button
							type="button"
							disabled={!ds.enabled}
							title={ds.enabled
								? undefined
								: 'Disabled — enable this dataset under Datasets to link it.'}
							class={[
								baseOpt,
								on ? selOpt : idleOpt,
								!ds.enabled && 'cursor-not-allowed opacity-50'
							]}
							onclick={() => ds.enabled && ontoggle?.(ds.code)}
						>
							<Checkbox
								checked={on}
								disabled={!ds.enabled}
								tabindex={-1}
								aria-label={ds.name}
								class="pointer-events-none"
							/>
							<span
								class="flex size-9 shrink-0 items-center justify-center rounded-lg {sm.bgColor}"
							>
								<DatabaseIcon class="size-4.5 {sm.textColor}" />
							</span>
							<span class="min-w-0 flex-1">
								<span class="block truncate text-sm font-medium">{ds.name}</span>
								<span class="text-muted-foreground block truncate font-mono text-xs">{ds.code}</span
								>
							</span>
							<span class="flex shrink-0 flex-col items-end gap-1">
								<span
									class="rounded-full px-2 py-0.5 text-xs font-medium {sm.bgColor} {sm.textColor}"
								>
									{sm.label}
								</span>
								<span class="text-muted-foreground text-[11px]">{scope.label}</span>
							</span>
						</button>
					{/each}
				</div>

				{#if filtered.length === 0}
					<div
						class="text-muted-foreground flex flex-col items-center justify-center gap-2 py-12 text-center text-sm"
					>
						<SearchIcon class="size-8 opacity-40" />
						No datasets match your filters.
					</div>
				{/if}
			{/if}
		</div>

		<!-- Footer -->
		<div class="flex items-center justify-between border-t px-6 py-4">
			<span class="text-muted-foreground text-xs">
				{linked.length} linked · {filtered.length} of {datasets.length} shown
			</span>
			<Button onclick={() => (open = false)}>Done</Button>
		</div>
	</Dialog.Content>
</Dialog.Root>
