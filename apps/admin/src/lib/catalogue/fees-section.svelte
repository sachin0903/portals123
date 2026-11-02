<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Badge,
		Button,
		Card,
		CardContent,
		CELL_TEXT,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		Input,
		Label,
		Segmented,
		sentenceCase,
		SelectMenu,
		Sheet,
		Switch,
		Textarea,
		toast,
		TruncatedText
	} from '@graphene/ui';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import {
		ALL,
		type FilterDef,
		humanise,
		ListingToolbar,
		matches,
		optionsFrom,
		search
	} from '@graphene/ui/book/listing';
	import {
		ACCESS,
		CHARGE_BASES,
		FEE_TYPES,
		FREQUENCIES,
		PAYERS,
		describeFee,
		type FeePackage,
		type FeeTier,
		type Model,
		type Product
	} from './types';

	let {
		fees,
		products,
		models,
		canManage,
		form
	}: {
		fees: FeePackage[];
		products: Product[];
		models: Model[];
		canManage: boolean;
		form: { error?: string; saved?: string } | null;
	} = $props();

	let editing = $state<FeePackage | null>(null);
	let creating = $state(false);
	let sort = $state<DataTableSort | null>({ key: 'sort_order', dir: 'asc' });

	// Search + filters, client-side like models-section: `fees` arrives whole from the layout
	// loader, so narrowing a firm's fee packages is a predicate rather than a round trip.
	let query = $state('');
	/** The filter bar's state. Frequency is NOT in it — see `frequencyOptions`: a closed two-value
	 *  vocabulary reads as a switch, and burying it in a bar costs two clicks to reach one of two
	 *  answers. */
	let barValue = $state<Record<string, string[]>>({});
	const pick = (key: string) => barValue[key] ?? [];
	let filterFrequency = $state(ALL);
	// `category` is free text (a firm can type any string into it), so its options come from the
	// rows themselves — a filter can never offer a category that would return nothing. Frequency,
	// fee_type and charge_basis are closed vocabularies (FREQUENCIES/FEE_TYPES/CHARGE_BASES already
	// declared in types.ts), so their options are the fixed lists instead.
	const categoryOptions = $derived(optionsFrom(fees, (f) => f.category));
	// `aum` is the one key here that is an acronym, so it is the one sentenceCase gets wrong.
	const optionLabel = (value: string) => (value === 'aum' ? 'AUM' : sentenceCase(value));
	// Frequency is a closed two-value vocabulary, so it reads as a switch (Segmented) rather than a
	// dropdown that costs a click to reveal the second option. `ALL` stays the first segment so "no
	// filter" is still reachable — this is a three-state control, not a boolean toggle.
	const frequencyOptions = [
		{ value: ALL, label: 'All' },
		...FREQUENCIES.map((value) => ({ value, label: humanise(value) }))
	];
	const feeTypeOptions = FEE_TYPES.map((value) => ({ value, label: optionLabel(value) }));
	const chargeBasisOptions = CHARGE_BASES.map((value) => ({ value, label: optionLabel(value) }));
	const filtered = $derived(
		fees.filter(
			(fee) =>
				search(query, fee.name, fee.category, fee.product_name, fee.model_name) &&
				matches(pick('category'), fee.category) &&
				matches(filterFrequency, fee.frequency) &&
				matches(pick('fee_type'), fee.fee_type) &&
				matches(pick('charge_basis'), fee.charge_basis)
		)
	);
	const dirty = $derived(Object.keys(barValue).length > 0 || filterFrequency !== ALL);
	function clearFilters(): void {
		query = '';
		barValue = {};
		filterFrequency = ALL;
	}

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'category', label: 'Category', options: categoryOptions },
		{ key: 'fee_type', label: 'Charge type', options: feeTypeOptions },
		{ key: 'charge_basis', label: 'Charge basis', options: chargeBasisOptions }
	]);

	// The rule shape follows the type, so the editor swaps with it. Kept as local state rather
	// than derived from the form: a half-built tier ladder must survive switching field focus.
	let feeType = $state<'fixed' | 'percentage' | 'tiered'>('percentage');
	let amount = $state(0);
	let rate = $state(0);
	let tiers = $state<FeeTier[]>([{ from: 0, to: null, rate: 0 }]);

	const rule = $derived(
		feeType === 'fixed'
			? { amount: Number(amount) }
			: feeType === 'percentage'
				? { rate: Number(rate) }
				: {
						tiers: tiers.map((t) => ({
							...t,
							from: Number(t.from),
							to: t.to === null ? null : Number(t.to),
							rate: Number(t.rate)
						}))
					}
	);

	// Bands must be contiguous — the API rejects a gap (charges nothing in it) or an overlap
	// (charges twice), so the editor shows the same rule before saving.
	const bandError = $derived.by(() => {
		if (feeType !== 'tiered') return null;
		let previous: number | null = 0;
		for (const [index, tier] of tiers.entries()) {
			if (previous === null) return 'An open-ended band must be last.';
			if (Number(tier.from) !== previous) return `Band ${index + 1} should start at ${previous}.`;
			if (tier.to !== null && Number(tier.to) <= Number(tier.from))
				return `Band ${index + 1} ends before it starts.`;
			previous = tier.to === null ? null : Number(tier.to);
		}
		return null;
	});

	function open(fee: FeePackage | null) {
		if (fee) {
			feeType = fee.fee_type;
			amount = Number(fee.rule.amount ?? 0);
			rate = Number(fee.rule.rate ?? 0);
			tiers = (fee.rule.tiers ?? [{ from: 0, to: null, rate: 0 }]).map((t) => ({ ...t }));
			editing = fee;
		} else {
			feeType = 'percentage';
			amount = 0;
			rate = 0;
			tiers = [{ from: 0, to: null, rate: 0 }];
			creating = true;
		}
	}

	const items = (values: readonly string[], blank = false) => [
		...(blank ? [{ value: '', label: '—' }] : []),
		...values.map((value) => ({ value, label: optionLabel(value) }))
	];

	const columns: DataTableColumn<FeePackage>[] = [
		{ key: 'name', header: 'Fee package', sortable: true, headerClass: 'w-[30%]' },
		{ key: 'shape', header: 'Charge', headerClass: 'w-[14%]' },
		{
			key: 'frequency',
			header: 'Frequency',
			sortable: true,
			hideBelow: 'md',
			headerClass: 'w-[14%]'
		},
		{
			key: 'applies',
			header: 'Applies to',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (fee) => fee.applies_to?.length ?? 0,
			hideBelow: 'lg',
			headerClass: 'w-[14%]'
		},
		{
			key: 'window',
			header: 'Window',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (fee) => fee.effective_from ?? '',
			hideBelow: 'lg',
			headerClass: 'w-[18%]'
		},
		// `min-w-40`: up to three badges (Default/Optional/Off) sit side by side, unwrapped.
		{ key: 'flags', header: '', align: 'right', headerClass: 'w-[10%] min-w-40' }
	];

	$effect(() => {
		if (form?.saved === 'fee') {
			toast.success('Fee package saved');
			creating = false;
			editing = null;
		} else if (form?.error) {
			toast.error(form.error);
		}
	});
</script>

<div class="space-y-4">
	{#if canManage}
		<div class="flex justify-end">
			<Button onclick={() => open(null)}>
				<PlusIcon class="mr-1.5 size-4" /> New fee package
			</Button>
		</div>
	{/if}
	<Card glass>
		<CardContent class="space-y-3">
			<ListingToolbar
				bind:query
				placeholder="Search name, category, product or model"
				stacked
				{dirty}
				onclear={clearFilters}
				{filterDefs}
				filterValue={barValue}
				onfilters={(next) => (barValue = next)}
			>
				{#snippet filters()}
					<Segmented
						bind:value={filterFrequency}
						options={frequencyOptions}
						size="sm"
						label="Filter by frequency"
					/>
				{/snippet}
			</ListingToolbar>
			<DataTable
				{columns}
				rows={filtered}
				bind:sort
				getKey={(f) => f.id}
				fixed
				empty={fees.length ? 'No fee packages match that search.' : 'No fee packages yet.'}
			>
				{#snippet cell(fee, column)}
					{#if column.key === 'name'}
						<button
							type="button"
							class="text-left font-medium hover:underline"
							onclick={() => open(fee)}
						>
							{fee.name}
						</button>
						<span class="text-muted-foreground block truncate text-xs">
							{fee.category}{fee.product_name ? ` · ${fee.product_name}` : ''}{fee.model_name
								? ` · ${fee.model_name}`
								: ''}
						</span>
					{:else if column.key === 'shape'}
						<span class="tabular-nums">{describeFee(fee)}</span>
						<span class="text-muted-foreground block text-xs">
							{humanise(fee.fee_type)}{fee.charge_basis ? ` on ${humanise(fee.charge_basis)}` : ''}
						</span>
					{:else if column.key === 'frequency'}
						<Badge variant="outline" class="font-normal">{humanise(fee.frequency)}</Badge>
					{:else if column.key === 'applies'}
						<!-- `join(', ')` over a list with no upper bound — the same shape text-shell.ts names as
						     the widest thing in several listings (`advisers.join(', ')`). -->
						<TruncatedText
							text={fee.applies_to?.length ? fee.applies_to.join(', ') : 'everything'}
							class="{CELL_TEXT} text-muted-foreground text-xs"
						/>
					{:else if column.key === 'window'}
						<span class="text-muted-foreground text-xs">
							{fee.effective_from ?? '—'} → {fee.effective_to ?? 'open'}
						</span>
					{:else if column.key === 'flags'}
						<div class="flex items-center justify-end gap-1">
							{#if fee.is_default}<Badge variant="secondary">Default</Badge>{/if}
							{#if fee.optional}<Badge variant="outline">Optional</Badge>{/if}
							{#if !fee.enabled}<Badge variant="destructive">Off</Badge>{/if}
						</div>
					{/if}
				{/snippet}
			</DataTable>
		</CardContent>
	</Card>
</div>

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
			<Sheet.Title>{editing ? editing.name : 'New fee package'}</Sheet.Title>
			<Sheet.Description>The rule shape follows the type.</Sheet.Description>
		</Sheet.Header>
		<form
			method="POST"
			action="?/fee"
			use:enhance
			class="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pb-6"
		>
			{#if editing}<input type="hidden" name="uid" value={editing.uid} />{/if}
			<input type="hidden" name="rule" value={JSON.stringify(rule)} />
			<input type="hidden" name="fee_type" value={feeType} />

			<div class="grid gap-3 sm:grid-cols-2">
				<div class="space-y-1 sm:col-span-2">
					<Label for="f_name">Name</Label>
					<Input id="f_name" name="name" value={editing?.name ?? ''} required />
				</div>
				<div class="space-y-1">
					<Label for="category">Category</Label>
					<Input id="category" name="category" value={editing?.category ?? 'platform'} required />
				</div>
				<div class="space-y-1">
					<Label for="frequency">Frequency</Label>
					<SelectMenu
						id="frequency"
						name="frequency"
						items={items(FREQUENCIES)}
						value={editing?.frequency ?? 'ongoing'}
					/>
				</div>
				<div class="space-y-1 sm:col-span-2">
					<Label for="external_id">Provider fee code</Label>
					<Input id="external_id" name="external_id" value={editing?.external_id ?? ''} />
					<p class="text-muted-foreground text-xs">
						The provider's own code for this fee. For a SEI one-off (initial) fee charged ad hoc,
						the SEI FEE_TYPE code the charge is sent under. Leave blank for a Seccl or synced
						package.
					</p>
				</div>
			</div>

			<div class="space-y-2 rounded border p-3">
				<Label>Charge type</Label>
				<div class="flex gap-2">
					{#each FEE_TYPES as type (type)}
						<Button
							type="button"
							size="sm"
							variant={feeType === type ? 'default' : 'outline'}
							onclick={() => (feeType = type)}>{optionLabel(type)}</Button
						>
					{/each}
				</div>

				{#if feeType === 'fixed'}
					<div class="space-y-1">
						<Label for="amount">Amount (£)</Label>
						<Input id="amount" type="number" step="0.01" bind:value={amount} />
					</div>
				{:else if feeType === 'percentage'}
					<div class="space-y-1">
						<Label for="rate">Rate (%)</Label>
						<Input id="rate" type="number" step="0.0001" bind:value={rate} />
					</div>
				{:else}
					<div class="space-y-2">
						{#each tiers as tier, index (index)}
							<div class="flex items-center gap-2">
								<Input
									type="number"
									class="w-28 text-right tabular-nums"
									bind:value={tiers[index].from}
									placeholder="from"
								/>
								<span class="text-muted-foreground text-xs">→</span>
								<Input
									type="number"
									class="w-28 text-right tabular-nums"
									value={tier.to ?? ''}
									placeholder="open"
									oninput={(event) => {
										const raw = (event.currentTarget as HTMLInputElement).value;
										tiers[index].to = raw === '' ? null : Number(raw);
									}}
								/>
								<Input
									type="number"
									step="0.0001"
									class="w-24 text-right tabular-nums"
									bind:value={tiers[index].rate}
									placeholder="%"
								/>
								{#if tiers.length > 1}
									<Button
										type="button"
										variant="ghost"
										size="sm"
										onclick={() => (tiers = tiers.filter((_, i) => i !== index))}>×</Button
									>
								{/if}
							</div>
						{/each}
						<Button
							type="button"
							variant="ghost"
							size="sm"
							onclick={() => {
								const last = tiers[tiers.length - 1];
								const start = last?.to ?? Number(last?.from ?? 0) + 1;
								tiers = [
									...tiers.map((t, i) =>
										i === tiers.length - 1 && t.to === null ? { ...t, to: start } : t
									),
									{ from: start, to: null, rate: 0 }
								];
							}}>+ band</Button
						>
						{#if bandError}
							<p class="text-destructive text-xs">{bandError}</p>
						{/if}
					</div>
				{/if}
			</div>

			<div class="grid gap-3 sm:grid-cols-2">
				<div class="space-y-1">
					<Label for="charge_basis">Charged on</Label>
					<SelectMenu
						id="charge_basis"
						name="charge_basis"
						items={items(CHARGE_BASES, true)}
						value={editing?.charge_basis ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="payer">Paid by</Label>
					<SelectMenu
						id="payer"
						name="payer"
						items={items(PAYERS, true)}
						value={editing?.payer ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="minimum_amount">Minimum (£)</Label>
					<Input
						id="minimum_amount"
						name="minimum_amount"
						type="number"
						step="0.01"
						value={editing?.minimum_amount ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="maximum_amount">Maximum (£)</Label>
					<Input
						id="maximum_amount"
						name="maximum_amount"
						type="number"
						step="0.01"
						value={editing?.maximum_amount ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="effective_from">Effective from</Label>
					<Input
						id="effective_from"
						name="effective_from"
						type="date"
						value={editing?.effective_from ?? ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="effective_to">Effective to</Label>
					<Input
						id="effective_to"
						name="effective_to"
						type="date"
						value={editing?.effective_to ?? ''}
					/>
				</div>
				<div class="space-y-1 sm:col-span-2">
					<Label for="applies_to">Applies to (instrument types, comma separated)</Label>
					<Input
						id="applies_to"
						name="applies_to"
						value={editing?.applies_to?.join(', ') ?? ''}
						placeholder="Equity, Fund"
					/>
				</div>
				<div class="space-y-1">
					<Label for="product_id">Product</Label>
					<SelectMenu
						id="product_id"
						name="product_id"
						items={[
							{ value: '', label: 'Any product' },
							...products.map((p) => ({ value: String(p.id), label: p.name }))
						]}
						value={editing?.product_id ? String(editing.product_id) : ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="model_id">Model</Label>
					<SelectMenu
						id="model_id"
						name="model_id"
						items={[
							{ value: '', label: 'Any model' },
							...models.map((m) => ({ value: String(m.id), label: m.name }))
						]}
						value={editing?.model_id ? String(editing.model_id) : ''}
					/>
				</div>
				<div class="space-y-1">
					<Label for="access">Adviser access</Label>
					<SelectMenu
						id="access"
						name="access"
						items={items(ACCESS)}
						value={editing?.access ?? 'hidden'}
					/>
				</div>
				<div class="space-y-1">
					<Label for="f_sort">Sort order</Label>
					<Input id="f_sort" name="sort_order" type="number" value={editing?.sort_order ?? 0} />
				</div>
				<div class="space-y-1 sm:col-span-2">
					<Label for="f_description">Description</Label>
					<Textarea id="f_description" name="description" value={editing?.description ?? ''} />
				</div>
			</div>

			<div class="grid gap-2 sm:grid-cols-2">
				<div class="flex items-center justify-between rounded border px-3 py-2">
					<Label for="f_vatable">VAT applies</Label>
					<Switch id="f_vatable" name="vatable" checked={editing?.vatable ?? false} />
				</div>
				<div class="flex items-center justify-between rounded border px-3 py-2">
					<Label for="is_default">Default</Label>
					<Switch id="is_default" name="is_default" checked={editing?.is_default ?? false} />
				</div>
				<div class="flex items-center justify-between rounded border px-3 py-2">
					<Label for="optional">Optional</Label>
					<Switch id="optional" name="optional" checked={editing?.optional ?? false} />
				</div>
				<div class="flex items-center justify-between rounded border px-3 py-2">
					<Label for="f_enabled">Enabled</Label>
					<Switch id="f_enabled" name="enabled" checked={editing?.enabled ?? true} />
				</div>
			</div>

			<div class="flex gap-2">
				<Button type="submit" disabled={!canManage || bandError !== null}>Save fee package</Button>
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
