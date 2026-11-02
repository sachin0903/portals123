<script lang="ts">
	// The models list. Selecting a row opens the detail drawer (data / schema / definition) —
	// previously an inline panel under the table, which pushed the list off screen and made
	// comparing two models impossible.
	import {
		Badge,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		sentenceCase
	} from '@graphene/ui';
	import {
		type FilterDef,
		ListingToolbar,
		matches,
		optionsFrom,
		search
	} from '@graphene/ui/book/listing';
	import { MAT_GLYPH, STATE_LABEL, STATE_VAR, stateBadgeVariant } from '../status';
	import type { ChangeState, LineageModel, Materialisation } from '../types';
	import type { PlatformStore } from '../store.svelte';
	import ModelDrawer from './ModelDrawer.svelte';

	let { store }: { store: PlatformStore } = $props();

	// Search + filters, client-side: `store.data.models` is the whole compiled DAG for this
	// platform, loaded once — narrowing it is a predicate, not a round trip. Was an ad-hoc Input
	// (name only) plus a Segmented for materialisation; replaced with the house ListingToolbar +
	// FilterBar pattern for consistency with the rest of the estate, and to gain a state/owner
	// filter without inventing a second filter idiom.
	let query = $state('');
	/** The filter bar's state. ONE object: the bar hands back the whole selection. */
	let barValue = $state<Record<string, string[]>>({});
	const pick = (key: string) => barValue[key] ?? [];
	let drawerOpen = $state(false);

	// Materialisation and change-state are closed vocabularies with their own label maps already
	// (MAT_GLYPH, STATE_LABEL) — fixed lists rather than "whatever happens to be used", so a run
	// with nothing currently `rebuild` still offers it. Owner has no such vocabulary, so its
	// options come from the rows themselves via `optionsFrom`.
	const MAT_VALUES: Materialisation[] = ['stream', 'table', 'view', 'ephemeral', 'sink'];
	const matOptions = MAT_VALUES.map((value) => ({
		value,
		label: `${MAT_GLYPH[value]} ${sentenceCase(value)}`
	}));
	const STATE_VALUES: ChangeState[] = ['unchanged', 'added', 'nonbreaking', 'breaking'];
	const stateOptions = STATE_VALUES.map((value) => ({ value, label: STATE_LABEL[value] }));
	const ownerOptions = $derived(optionsFrom(store.data.models, (m) => m.owner));

	// `state` and `mat` are BADGED, and `running` outranks the stored state on screen — so both
	// say what to order by rather than sorting the raw enum, which would scatter the running
	// models through the list instead of gathering them.
	const modelColumns: DataTableColumn<LineageModel>[] = [
		{ key: 'name', header: 'Model', sortable: true, sortDefault: true },
		{ key: 'layer', header: 'Layer', sortable: true },
		{ key: 'mat', header: 'Materialisation', sortable: true },
		{ key: 'strategy', header: 'Strategy', sortable: true },
		{
			key: 'state',
			header: 'State',
			sortable: true,
			sortValue: (m) => (m.running ? 'running' : STATE_LABEL[m.state])
		},
		{ key: 'rows', header: 'Rows', sortable: true, align: 'right', defaultDir: 'desc' },
		{ key: 'schedule', header: 'Schedule', sortable: true }
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, so a literal passed in
	// the template is recreated on every update and stamps the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let modelSort = $state<DataTableSort | null>(null);

	const filtered = $derived(
		store.data.models.filter(
			(m) =>
				search(query, m.name, m.owner, m.schedule, m.tags.join(' ')) &&
				matches(pick('mat'), m.mat) &&
				matches(pick('state'), m.state) &&
				matches(pick('owner'), m.owner)
		)
	);
	const dirty = $derived(Object.keys(barValue).length > 0);

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'mat', label: 'Materialisation', options: matOptions },
		{ key: 'state', label: 'State', options: stateOptions },
		{ key: 'owner', label: 'Owner', options: ownerOptions }
	]);
	function clearFilters(): void {
		query = '';
		barValue = {};
	}
</script>

<div class="space-y-4">
	<ListingToolbar
		bind:query
		placeholder="Search name, owner or schedule"
		{dirty}
		onclear={clearFilters}
		{filterDefs}
		filterValue={barValue}
		onfilters={(next) => (barValue = next)}
	></ListingToolbar>
	<p class="text-muted-foreground text-sm">
		{filtered.length} of {store.data.models.length} models
		<span class="text-muted-foreground">
			· rebuild / refresh is the compiled plan for the next run, not a pending apply
		</span>
	</p>

	<div class="overflow-hidden rounded-xl border">
		<DataTable
			columns={modelColumns}
			rows={filtered}
			bind:sort={modelSort}
			getKey={(model) => model.name}
			label="Models"
			rowClass={(model) => (store.selected === model.name ? 'bg-accent/60' : '')}
			onRowClick={(model) => {
				store.select(model.name);
				drawerOpen = true;
			}}
		>
			{#snippet cell(model, column)}
				{#if column.key === 'name'}
					<span class="font-mono text-xs font-medium">{model.name}</span>
				{:else if column.key === 'layer'}
					<span class="text-muted-foreground text-xs">{model.layer}</span>
				{:else if column.key === 'mat'}
					<span class="font-mono text-xs">{MAT_GLYPH[model.mat]} {model.mat}</span>
				{:else if column.key === 'strategy'}
					<span class="text-muted-foreground font-mono text-xs">{model.strategy}</span>
				{:else if column.key === 'state'}
					{#if model.running}
						<Badge variant="secondary" class="gap-1.5">
							<span class="size-1.5 rounded-full" style="background: var(--ring)"></span>
							running
						</Badge>
					{:else}
						<Badge variant={stateBadgeVariant(model.state)} class="gap-1.5">
							<span class="size-1.5 rounded-full" style="background: var({STATE_VAR[model.state]})"
							></span>
							{STATE_LABEL[model.state]}
						</Badge>
					{/if}
				{:else if column.key === 'rows'}
					<span class="font-mono text-xs tabular-nums">{model.rows}</span>
				{:else if column.key === 'schedule'}
					<span class="text-muted-foreground font-mono text-xs">{model.schedule}</span>
				{/if}
			{/snippet}
		</DataTable>
	</div>
</div>

<ModelDrawer bind:open={drawerOpen} {store} />
