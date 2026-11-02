<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	/** Both are multi: a model has one structure but several products, matched with
	 *  `matches`/`matchesAny` respectively. */
	export function modelFilterDefs(input: {
		structureOptions: FilterDef['options'];
		productOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{ key: 'structure', label: 'Structure', options: input.structureOptions },
			{ key: 'product', label: 'Product', options: input.productOptions }
		];
	}
</script>

<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import {
		Badge,
		Button,
		Card,
		CardContent,
		Checkbox,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		humanise,
		Input,
		Label,
		MultiSelect,
		RecordPicker,
		type RecordOption,
		RiskBadge,
		SelectMenu,
		Sheet,
		Switch,
		Textarea,
		toast
	} from '@graphene/ui';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import {
		ListingToolbar,
		matches,
		matchesAny,
		optionsFrom,
		search
	} from '@graphene/ui/book/listing';
	import {
		INVESTMENT_DECISIONS,
		RISK_RATINGS,
		allocationTotal,
		type Allocation,
		type Model,
		type ModelDetail
	} from './types';

	let {
		models,
		detail,
		canManage,
		seiEnabled = false,
		seiDsrOptions = [],
		modelDsrs = {},
		form
	}: {
		models: Model[];
		detail: ModelDetail | null;
		canManage: boolean;
		/** SEI is enabled for this firm, so a model's SEI DSR teams can be assigned here (moved off
		 *  the SEI platform card). The options are the firm's DSR teams; `modelDsrs` is the current
		 *  assignment keyed by model uid. */
		seiEnabled?: boolean;
		seiDsrOptions?: { value: string; label: string }[];
		modelDsrs?: Record<string, string[]>;
		form: { error?: string; saved?: string } | null;
	} = $props();

	// The line stores the instrument id. The picker searches the whole universe: the
	// catalogue bundle is the first 500 names, and a fund outside that slice cannot be
	// allocated if those are the only options. The list is in the document flow because
	// this editor sits in a sheet, and an overlay there never receives the click.

	let editing = $state<Model | null>(null);
	let creating = $state(false);
	let sort = $state<DataTableSort | null>({ key: 'name', dir: 'asc' });

	// Search + filters, client-side like the book listings: 51 models on the dev firm arrive in
	// one call, so narrowing them is a predicate rather than a round trip.
	let query = $state('');
	let structureFilter = $state<string[]>([]);
	let productFilter = $state<string[]>([]);
	// Both option lists come from the rows themselves, so a filter can never offer a value that
	// returns nothing — and `products` is multi-valued (a model is available for several
	// wrappers), which is what `matchesAny` is for.
	const structures = $derived(optionsFrom(models, (m) => m.model_type));
	const productOptions = $derived(optionsFrom(models, (m) => m.products ?? []));
	const filtered = $derived(
		models.filter(
			(m) =>
				search(query, m.name, m.code, m.aim, m.benchmark) &&
				matches(structureFilter, m.model_type) &&
				matchesAny(productFilter, m.products ?? [])
		)
	);
	const dirty = $derived(structureFilter.length > 0 || productFilter.length > 0);
	function clearFilters(): void {
		query = '';
		structureFilter = [];
		productFilter = [];
	}

	const filterDefs = $derived(modelFilterDefs({ structureOptions: structures, productOptions }));
	const barValue = $derived<Record<string, string[]>>({
		...(structureFilter.length ? { structure: structureFilter } : {}),
		...(productFilter.length ? { product: productFilter } : {})
	});
	function setFilters(next: Record<string, string[]>): void {
		structureFilter = next.structure ?? [];
		productFilter = next.product ?? [];
	}

	// The allocation editor. Lines are local until saved, because a version is one atomic
	// change — a half-entered allocation must not reach the API, which would reject it anyway
	// for not totalling 100.
	let lines = $state<Allocation[]>([]);
	let cash = $state(0);
	let composing = $state(false);
	const total = $derived(allocationTotal(lines, cash));

	// Markdown, deliberately: it is what a client report re-renders, and stored HTML would have
	// to be sanitised on every read.
	let commentaryBody = $state('');
	let commentaryTitle = $state('');
	let commentaryVersion = $state('');

	const BASES = [
		{ value: '', label: '—' },
		{ value: 'absolute', label: 'Absolute' },
		{ value: 'above_inflation', label: 'Above inflation (CPI)' },
		{ value: 'above_benchmark', label: 'Above benchmark' }
	];
	const items = (values: readonly string[], blank = false) => [
		...(blank ? [{ value: '', label: '—' }] : []),
		...values.map((value) => ({ value, label: value }))
	];
	// Seccl's own word, so what is picked here is what the API will accept on the way back out.
	let decision = $state('');

	const columns: DataTableColumn<Model>[] = [
		// Capped and truncated: model names run to "Marlborough MPS Passive Risk Grade 8" and
		// wider, which pushed every other column off the row.
		{
			key: 'name',
			header: 'Model',
			sortable: true,
			headerClass: 'w-[24%]',
			cellClass: 'max-w-[20rem]'
		},
		{
			key: 'products',
			header: 'Products',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (m) => m.products?.length ?? 0,
			hideBelowWidth: '2xl',
			headerClass: 'w-[16%]'
		},
		{
			key: 'target',
			header: 'Target return',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (m) => Number(m.target_return ?? 0),
			headerClass: 'w-[14%]'
		},
		{
			key: 'risk',
			header: 'Risk',
			sortable: true,
			hideBelow: 'md',
			sortValue: (m) => m.risk_rating ?? '',
			headerClass: 'w-[12%]'
		},
		{
			key: 'decision',
			header: 'Decision',
			sortable: true,
			sortValue: (m) => m.investment_decision ?? '',
			hideBelow: 'lg',
			headerClass: 'w-[14%]'
		},
		{
			key: 'live',
			header: 'Live version',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (m) => m.live_version ?? 0,
			headerClass: 'w-[12%]'
		},
		{
			key: 'history',
			header: 'Allocations',
			align: 'right',
			headerClass: 'w-[14%] min-w-36'
		}
	];

	function targetLabel(model: Model): string {
		if (model.target_return === null) return '—';
		const basis =
			model.target_return_basis === 'above_inflation'
				? ' above CPI'
				: model.target_return_basis === 'above_benchmark'
					? ' above benchmark'
					: '';
		const horizon = model.target_return_horizon_years
			? ` over ${model.target_return_horizon_years}y`
			: '';
		return `${model.target_return}%${basis}${horizon}`;
	}

	// Seed the pickers from the row being edited; `untrack` is unnecessary because `editing` is
	// local state that only changes on an explicit open.
	$effect(() => {
		decision = editing?.investment_decision ?? '';
	});

	function openHistory(model: Model) {
		// The name opens the model's own fields. Allocations are a different sheet, and the
		// edit sheet used to be the only thing a click revealed — with a note that the
		// holdings lived somewhere else.
		creating = false;
		editing = null;
		const url = new URL(page.url);
		url.searchParams.set('model', model.uid);
		url.searchParams.set('section', 'models');
		goto(url, { keepFocus: true, noScroll: true });
	}

	function closeHistory() {
		const url = new URL(page.url);
		url.searchParams.delete('model');
		goto(url, { keepFocus: true, noScroll: true });
	}

	function blankLine(): Allocation {
		return {
			instrument_id: null,
			external_asset_id: null,
			isin: '',
			name: '',
			percentage: 0
		};
	}

	// A model with no holdings opens straight onto the editor. Remembering which model
	// already did this lets Cancel stay cancelled.
	let composerFor = $state<string | null>(null);
	$effect(() => {
		const uid = detail?.uid ?? null;
		if (uid === composerFor) return;
		composerFor = uid;
		if (detail && canManage && detail.versions.length === 0) {
			lines = [blankLine()];
			cash = 0;
			composing = true;
		} else {
			composing = false;
		}
	});

	function startVersion() {
		// Seed from the live version: a rebalance is usually an edit of what is already there,
		// and retyping twenty lines is how a percentage gets fat-fingered.
		const live = detail?.versions.find((v) => v.published_at) ?? detail?.versions[0];
		lines = (live?.allocations ?? []).map((line) => ({ ...line }));
		cash = Number(live?.cash_allocation ?? 0);
		composing = true;
	}

	function lineLabel(line: Allocation): string {
		if (line.name && line.isin) return `${line.name} · ${line.isin}`;
		return line.name || line.isin || '';
	}

	function searchInstruments(query: string): Promise<RecordOption[]> {
		const url = new URL(`/tenants/${page.params.uid}/platform/instruments/search`, location.origin);
		if (query.trim()) url.searchParams.set('q', query.trim());
		return fetch(url).then(async (res) => {
			if (!res.ok) throw new Error('Could not search instruments.');
			return (await res.json()) as RecordOption[];
		});
	}

	function chooseInstrument(index: number, option: RecordOption | null) {
		const next = lines.map((line) => ({ ...line }));
		const line = next[index];
		if (!line) return;
		if (!option) {
			line.instrument_id = null;
			line.isin = null;
			line.name = null;
		} else {
			line.instrument_id = Number(option.value);
			line.name = option.label;
			line.isin = option.hint ?? null;
			line.external_asset_id = null;
		}
		lines = next;
	}

	$effect(() => {
		if (form?.saved === 'model') {
			toast.success('Model saved');
			creating = false;
			editing = null;
		} else if (form?.saved === 'version') {
			toast.success('Version saved as a draft');
			composing = false;
		} else if (form?.saved === 'published') {
			toast.success('Version published');
		} else if (form?.saved === 'commentary') {
			toast.success('Commentary saved');
			commentaryBody = '';
			commentaryTitle = '';
		} else if (form?.error) {
			toast.error(form.error);
		}
	});
</script>

<div class="space-y-4">
	{#if canManage}
		<div class="flex justify-end">
			<Button onclick={() => (creating = true)}>
				<PlusIcon class="mr-1.5 size-4" /> New model
			</Button>
		</div>
	{/if}
	<Card glass>
		<CardContent class="space-y-3">
			<ListingToolbar
				bind:query
				placeholder="Search name, code, aim or benchmark"
				{dirty}
				onclear={clearFilters}
				{filterDefs}
				filterValue={barValue}
				onfilters={setFilters}
			/>
			<DataTable
				{columns}
				rows={filtered}
				bind:sort
				getKey={(m) => m.id}
				fixed
				empty={models.length ? 'No models match that search.' : 'No models yet.'}
			>
				{#snippet cell(model, column)}
					{#if column.key === 'name'}
						<button
							type="button"
							class="block w-full truncate text-left font-medium hover:underline"
							title={model.name}
							onclick={() => (editing = model)}
						>
							{model.name}
						</button>
						{#if model.aim}
							<span class="text-muted-foreground block truncate text-xs">{model.aim}</span>
						{/if}
					{:else if column.key === 'products'}
						{#if model.products?.length}
							<div class="flex flex-wrap gap-1">
								{#each model.products as wrapper (wrapper)}
									<Badge variant="outline" class="font-normal">{wrapper}</Badge>
								{/each}
							</div>
						{:else}
							<span class="text-muted-foreground text-xs">—</span>
						{/if}
					{:else if column.key === 'target'}
						<span class="tabular-nums">{targetLabel(model)}</span>
					{:else if column.key === 'risk'}
						<RiskBadge rating={model.risk_rating} />
					{:else if column.key === 'decision'}
						{#if model.investment_decision}
							<Badge variant="outline" class="font-normal">
								{humanise(model.investment_decision)}
							</Badge>
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					{:else if column.key === 'live'}
						{#if model.live_version}
							<Badge variant="secondary">v{model.live_version}</Badge>
						{:else}
							<span class="text-muted-foreground text-xs">
								{model.version_count ? 'draft only' : 'none'}
							</span>
						{/if}
					{:else if column.key === 'history'}
						<Button variant="outline" size="sm" onclick={() => openHistory(model)}>
							Allocations
							{#if model.version_count}
								<span class="text-muted-foreground ml-1.5 tabular-nums">{model.version_count}</span>
							{/if}
						</Button>
					{/if}
				{/snippet}
			</DataTable>
		</CardContent>
	</Card>
</div>

<!-- Create / edit the model itself -->
<Sheet.Root
	open={creating || editing !== null}
	onOpenChange={(next) => {
		if (!next) {
			creating = false;
			editing = null;
		}
	}}
>
	<Sheet.Content side="right" class="w-full">
		<Sheet.Header>
			<Sheet.Title>{editing ? editing.name : 'New model'}</Sheet.Title>
			<Sheet.Description>
				{#if editing}
					Holdings are a separate version of this model.
				{:else}
					Save the model first. Its allocations are set once it exists.
				{/if}
			</Sheet.Description>
		</Sheet.Header>
		{#if editing}
			<div class="px-4">
				<Button type="button" variant="outline" onclick={() => editing && openHistory(editing)}>
					Allocations
				</Button>
			</div>
		{/if}
		<form
			method="POST"
			action="?/model"
			use:enhance
			class="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pb-6"
		>
			{#if editing}<input type="hidden" name="uid" value={editing.uid} />{/if}
			<div class="grid gap-3 sm:grid-cols-2">
				<div class="space-y-1 sm:col-span-2">
					<Label for="m_name">Name</Label>
					<Input id="m_name" name="name" value={editing?.name ?? ''} required />
				</div>
				<div class="space-y-1 sm:col-span-2">
					<Label for="aim">Aim</Label>
					<Textarea id="aim" name="aim" value={editing?.aim ?? ''} />
				</div>
				<div class="space-y-1">
					<Label for="target_return">Target return (%)</Label>
					<Input
						id="target_return"
						name="target_return"
						type="number"
						step="0.01"
						value={editing?.target_return ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="target_return_basis">Basis</Label>
					<SelectMenu
						id="target_return_basis"
						name="target_return_basis"
						portal={false}
						items={BASES}
						value={editing?.target_return_basis ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="horizon">Horizon (years)</Label>
					<Input
						id="horizon"
						name="target_return_horizon_years"
						type="number"
						value={editing?.target_return_horizon_years ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="benchmark">Benchmark</Label>
					<Input id="benchmark" name="benchmark" value={editing?.benchmark ?? ''} />
				</div>
				<div class="space-y-1">
					<Label for="risk_rating">Risk rating</Label>
					<SelectMenu
						id="risk_rating"
						name="risk_rating"
						portal={false}
						items={items(RISK_RATINGS, true)}
						value={editing?.risk_rating ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="risk_profile">Risk profile</Label>
					<Input id="risk_profile" name="risk_profile" value={editing?.risk_profile ?? ''} />
				</div>
				<div class="space-y-1 sm:col-span-2">
					<Label>Model type</Label>
					<input type="hidden" name="investment_decision" value={decision} />
					<div class="flex gap-2">
						{#each INVESTMENT_DECISIONS as option (option)}
							<Button
								type="button"
								size="sm"
								variant={decision === option ? 'default' : 'outline'}
								onclick={() => (decision = option)}>{option}</Button
							>
						{/each}
					</div>
				</div>
				<!-- Structure is the PROVIDER's `modelType`, and the sync assigns it unconditionally
				     (`model_type = :model_type`, no coalesce) — so an edit here would survive until the
				     next run and no longer. Shown, not offered.

				     Instrument type and Rate type used to sit beside it and were pure decoration:
				     they are Seccl `assetMix` properties of an allocation LINE, platform.models has no
				     such columns, `_MODEL_FIELDS` does not list them, and the form action never posted
				     them — an operator could set either and nothing anywhere would keep it. -->
				<div class="space-y-1">
					<Label>Structure</Label>
					<p class="text-muted-foreground h-9 text-sm leading-9">
						{editing?.model_type ?? '—'}
						<span class="text-xs">· set by the custodian</span>
					</p>
				</div>
				<div class="space-y-1">
					<Label for="model_charge">Model charge (%)</Label>
					<Input
						id="model_charge"
						name="model_charge"
						type="number"
						step="0.01"
						value={editing?.model_charge ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="m_code">Code</Label>
					<Input id="m_code" name="code" value={editing?.code ?? ''} />
				</div>
			</div>
			<!-- This switch exists because the action always posted `vatable`, and with no control to
			     read it posted FALSE — so editing a model's NAME cleared the VAT flag the platform
			     had published on it. -->
			<div class="flex items-center justify-between rounded border px-3 py-2">
				<Label for="vatable">Charge is VATable</Label>
				<Switch id="vatable" name="vatable" checked={editing?.vatable ?? false} />
			</div>
			<div class="flex items-center justify-between rounded border px-3 py-2">
				<Label for="suspend_rebalances">Rebalances suspended</Label>
				<Switch
					id="suspend_rebalances"
					name="suspend_rebalances"
					checked={editing?.suspend_rebalances ?? false}
				/>
			</div>
			<div class="flex items-center justify-between rounded border px-3 py-2">
				<Label for="m_enabled">Enabled</Label>
				<Switch id="m_enabled" name="enabled" checked={editing?.enabled ?? true} />
			</div>
			<div class="flex gap-2">
				<Button type="submit" disabled={!canManage}>Save model</Button>
				<Button
					type="button"
					variant="ghost"
					onclick={() => {
						creating = false;
						editing = null;
					}}>Cancel</Button
				>
			</div>
		</form>
	</Sheet.Content>
</Sheet.Root>

<!-- Allocation history + commentary -->
<Sheet.Root
	open={detail !== null}
	onOpenChange={(next) => {
		if (!next) closeHistory();
	}}
>
	<Sheet.Content side="right" class="w-full">
		{#if detail}
			<Sheet.Header>
				<Sheet.Title>{detail.name}</Sheet.Title>
				<Sheet.Description>
					Each line is an instrument and its percentage. Cash is its own figure. The lines and cash
					must total 100%.
				</Sheet.Description>
			</Sheet.Header>

			<div class="min-h-0 flex-1 space-y-6 overflow-y-auto px-4 pb-8">
				{#if seiEnabled}
					<!-- SEI DSR teams — moved here off the SEI platform card so a model's matching is
					     edited on the model itself. -->
					<section class="space-y-2">
						<h3 class="text-sm font-semibold">SEI DSR teams</h3>
						<p class="text-muted-foreground text-xs">
							DSR teams added to the firm and custody grants when a new client or account uses this
							model.
						</p>
						{#key detail.uid}
							<form method="POST" action="?/modelSeiDsrs" use:enhance class="flex items-end gap-2">
								<input type="hidden" name="model_uid" value={detail.uid} />
								<MultiSelect
									class="min-w-0 flex-1"
									name="dsr_ids"
									value={modelDsrs[detail.uid] ?? []}
									options={seiDsrOptions}
									disabled={!canManage}
									placeholder="DSR teams (optional)"
									searchPlaceholder="Search DSR teams…"
									emptyText="No SEI DSR teams for this firm."
								/>
								{#if canManage}
									<Button type="submit" size="sm" variant="outline">Save</Button>
								{/if}
							</form>
						{/key}
					</section>
				{/if}

				<!-- Versions -->
				<section class="space-y-3">
					<div class="flex items-center justify-between">
						<h3 class="text-sm font-semibold">Allocations</h3>
						{#if canManage && !composing}
							<Button size="sm" variant="outline" onclick={startVersion}>New version</Button>
						{/if}
					</div>
					<p class="text-muted-foreground text-xs">
						Each line is an instrument and its percentage. Cash is its own figure. The lines and
						cash must total 100%.
					</p>

					{#if composing}
						<form
							method="POST"
							action="?/modelVersion"
							use:enhance
							class="space-y-3 rounded border p-3"
						>
							<input type="hidden" name="model_uid" value={detail.uid} />
							<input type="hidden" name="allocations" value={JSON.stringify(lines)} />
							{#each lines.keys() as index (index)}
								<div class="flex items-start gap-2">
									<RecordPicker
										class="min-w-0 flex-1"
										placeholder="Search instruments"
										emptyText="No instruments match."
										capNote="Showing the closest matches. Keep typing to narrow."
										inline
										label={lineLabel(lines[index])}
										value={lines[index].instrument_id ? String(lines[index].instrument_id) : ''}
										search={searchInstruments}
										onSelect={(option) => chooseInstrument(index, option)}
									/>
									<Input
										type="number"
										step="0.000001"
										min="0"
										aria-label="Percentage"
										class="w-24 text-right tabular-nums"
										bind:value={lines[index].percentage}
									/>
									<Button
										type="button"
										variant="ghost"
										size="sm"
										onclick={() => (lines = lines.filter((_, i) => i !== index))}>×</Button
									>
								</div>
							{/each}
							<Button
								type="button"
								variant="ghost"
								size="sm"
								onclick={() =>
									(lines = [
										...lines,
										{
											instrument_id: null,
											external_asset_id: null,
											isin: '',
											name: '',
											percentage: 0
										}
									])}>+ line</Button
							>
							<div class="flex items-end gap-3">
								<div class="space-y-1">
									<Label for="cash_allocation">Cash %</Label>
									<Input
										id="cash_allocation"
										name="cash_allocation"
										type="number"
										step="0.01"
										class="w-24 text-right tabular-nums"
										bind:value={cash}
									/>
								</div>
								<div class="space-y-1">
									<Label for="effective_from">Effective from</Label>
									<Input id="effective_from" name="effective_from" type="date" />
								</div>
								<!-- The total is shown before saving because the API rejects anything that is not
								     100 (±0.05), and finding that out after typing 20 lines is a bad trade. -->
								<div class="ml-auto text-right">
									<span class="text-muted-foreground block text-xs">Total</span>
									<span
										class="tabular-nums font-semibold"
										class:text-destructive={Math.abs(total - 100) > 0.05}>{total}%</span
									>
								</div>
							</div>
							<div class="space-y-1">
								<Label for="note">Note</Label>
								<Input id="note" name="note" placeholder="Q3 rebalance" />
							</div>
							<div class="flex gap-2">
								<Button type="submit" size="sm" disabled={Math.abs(total - 100) > 0.05}>
									Save draft
								</Button>
								<Button type="button" variant="ghost" size="sm" onclick={() => (composing = false)}>
									Cancel
								</Button>
							</div>
						</form>
					{/if}

					{#each detail.versions as version (version.id)}
						<div class="rounded border p-3">
							<div class="flex items-center justify-between gap-3">
								<div class="flex items-center gap-2">
									<Badge variant={version.published_at ? 'secondary' : 'outline'}>
										v{version.version}
									</Badge>
									{#if !version.published_at}
										<span class="text-muted-foreground text-xs">draft</span>
									{/if}
									{#if version.effective_from}
										<span class="text-muted-foreground text-xs">
											from {version.effective_from}
										</span>
									{/if}
									{#if version.note}<span class="text-xs">· {version.note}</span>{/if}
								</div>
								{#if canManage && !version.published_at}
									<form method="POST" action="?/publishVersion" use:enhance>
										<input type="hidden" name="model_uid" value={detail.uid} />
										<input type="hidden" name="version_uid" value={version.uid} />
										<Button type="submit" size="sm" variant="outline">Publish</Button>
									</form>
								{/if}
							</div>
							<ul class="mt-2 space-y-1">
								{#each version.allocations as line (line.id ?? line.isin)}
									<li class="flex justify-between text-xs">
										<span class="truncate">
											{line.name ?? line.isin ?? line.external_asset_id}
											{#if line.isin}
												<span class="text-muted-foreground font-mono">· {line.isin}</span>
											{/if}
										</span>
										<span class="tabular-nums">{line.percentage}%</span>
									</li>
								{/each}
								{#if Number(version.cash_allocation ?? 0) > 0}
									<li class="text-muted-foreground flex justify-between text-xs">
										<span>Cash</span><span class="tabular-nums">{version.cash_allocation}%</span>
									</li>
								{/if}
							</ul>
						</div>
					{:else}
						{#if !composing}
							<p class="text-muted-foreground text-sm">No allocations yet.</p>
						{/if}
					{/each}
				</section>

				<!-- Commentary -->
				<section class="space-y-3">
					<h3 class="text-sm font-semibold">Commentary</h3>
					{#if canManage}
						<form
							method="POST"
							action="?/commentary"
							use:enhance
							class="space-y-2 rounded border p-3"
						>
							<input type="hidden" name="model_uid" value={detail.uid} />
							<Input name="title" placeholder="Title" bind:value={commentaryTitle} />
							<Textarea
								name="body"
								rows={6}
								placeholder="Markdown. Headings, lists and links render in client reporting."
								bind:value={commentaryBody}
							/>
							<div class="flex items-center gap-2">
								<SelectMenu
									name="model_version_id"
									portal={false}
									items={[
										{ value: '', label: 'Not about a version' },
										...detail.versions.map((v) => ({
											value: String(v.id),
											label: `v${v.version}`
										}))
									]}
									bind:value={commentaryVersion}
								/>
								<Input name="period_start" type="date" class="w-40" />
								<Input name="period_end" type="date" class="w-40" />
							</div>
							<div class="flex items-center gap-3">
								<Button type="submit" size="sm" disabled={!commentaryBody.trim()}>Save</Button>
								<label class="flex items-center gap-2 text-xs">
									<Checkbox name="publish" value="true" /> Publish now
								</label>
							</div>
						</form>
					{/if}
					{#each detail.commentary as entry (entry.id)}
						<article class="rounded border p-3">
							<div class="flex items-center justify-between">
								<h4 class="text-sm font-medium">{entry.title ?? 'Untitled'}</h4>
								<Badge variant={entry.published_at ? 'secondary' : 'outline'}>
									{entry.published_at ? 'Published' : 'Draft'}
								</Badge>
							</div>
							<!-- Rendered as text on purpose: the stored body is markdown, and turning it into
							     HTML here would mean sanitising it on every read. -->
							<p class="text-muted-foreground mt-1 line-clamp-4 text-xs whitespace-pre-wrap">
								{entry.body}
							</p>
						</article>
					{/each}
				</section>
			</div>
		{/if}
	</Sheet.Content>
</Sheet.Root>
