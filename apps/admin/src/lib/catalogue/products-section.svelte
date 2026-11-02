<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		Card,
		CardContent,
		CELL_NAME,
		Checkbox,
		DataTable,
		Input,
		Label,
		MultiSelect,
		Select,
		Sheet,
		Switch,
		toast,
		TruncatedText,
		type DataTableColumn,
		type DataTableSort,
		type MultiSelectOption
	} from '@graphene/ui';
	import SettingsIcon from '@lucide/svelte/icons/settings-2';
	import {
		PRODUCT_TYPES,
		PRODUCT_TYPE_LABELS,
		type PlatformRef,
		type Product,
		type ProviderOption
	} from './types';

	/** 0122's three roles in words. Not a badge: the role qualifies the platform's name rather
	 *  than standing on its own, and a row of coloured pills would compete with the reference
	 *  that is the point of the cell. */
	const ROLE_LABELS: Record<NonNullable<PlatformRef['role']>, string> = {
		custodian: 'custodian',
		scheme_administrator: 'administrator',
		wrapper_provider: 'wrapper'
	};

	let {
		products,
		platforms,
		currencies,
		canManage,
		form,
		seiAccountTemplates = [],
		seiFirmId = null
	}: {
		products: Product[];
		/** The provider registry, served by the API from code (0131): `id` IS the provider code. */
		platforms: { id: string; name: string; provider: string }[];
		currencies: { code: string; symbol: string | null; currency: string | null }[];
		canManage: boolean;
		form: { error?: string; saved?: string } | null;
		/** Account-level enrolment templates for THIS tenant's enabled SEI firm. */
		seiAccountTemplates?: ProviderOption[];
		/** The firm's enabled SEI `firm_id`. Absent when SEI is not enabled for the tenant. */
		seiFirmId?: string | null;
	} = $props();

	// EVERY product type is a row: the shelf is a fixed vocabulary (platform.product_type), so a
	// firm decides what it offers by switching rows on and off — it does not author products.
	//
	// The platform references themselves are READ-ONLY. They are reference data, not
	// configuration: Seccl calls the SIPP wrapper PENSION and will keep calling it that until
	// Seccl supports a new product. There is nothing there for an operator to decide.
	//
	// THE EXCEPTION is the SEI enrolment template (`account_open_reference`). That id is per
	// shelf product, required to open an account, and a judgement only an operator can make —
	// which template belongs to this wrapper. The firm is the tenant's enabled SEI connection;
	// the settings sheet picks among that firm's templates.
	type Row = {
		product_type: string;
		name: string;
		code: string | null;
		enabled: boolean;
		allowed_custodies: ('onshore' | 'offshore')[];
		allowed_client_types: ('individual' | 'organisation')[];
		allowed_currencies: string[];
		max_parties: number;
		min_age: number | null;
		max_age: number | null;
		bank_details_allowed: boolean;
		refs: PlatformRef[];
		product: Product | null;
	};

	const byType = $derived(new Map(products.map((p) => [p.product_type, p])));
	const rows = $derived<Row[]>(
		PRODUCT_TYPES.map((product_type) => {
			const product = byType.get(product_type) ?? null;
			// NAME is what a person reads, CODE is what an API call carries. Discovery used to write
			// the type into `name` and leave `code` empty, so this table showed "TPONBOND" as the
			// product's name and nothing as its code — the two the wrong way round.
			//
			// A name equal to the type is that old placeholder (or a firm that has not renamed it),
			// so the label stands in; a firm's own name always wins.
			const label = PRODUCT_TYPE_LABELS[product_type] ?? product_type;
			return {
				product_type,
				name: product?.name && product.name !== product_type ? product.name : label,
				code: product?.code ?? product_type,
				enabled: product?.enabled ?? false,
				allowed_custodies: product?.allowed_custodies ?? ['onshore', 'offshore'],
				allowed_client_types:
					product?.allowed_client_types ??
					(['ISA', 'JISA'].includes(product_type)
						? ['individual']
						: ['individual', 'organisation']),
				allowed_currencies:
					product?.allowed_currencies ??
					(['ISA', 'JISA'].includes(product_type)
						? ['GBP']
						: currencies.map((currency) => currency.code)),
				max_parties: product?.max_parties ?? 2,
				min_age: product?.min_age ?? null,
				max_age: product?.max_age ?? (product_type === 'JISA' ? 17 : null),
				bank_details_allowed: product?.bank_details_allowed ?? product_type !== 'JISA',
				refs: product?.platforms ?? [],
				product
			};
		})
	);

	let sort = $state<DataTableSort | null>(null);

	const columns: DataTableColumn<Row>[] = [
		{
			key: 'name',
			header: 'Product',
			sortable: true,
			sortDefault: true,
			sortValue: (row) => row.name
		},
		{ key: 'code', header: 'Code', sortable: true, sortValue: (row) => row.code ?? '' },
		// The platform column is the widest and the least sortable thing here — a row can carry
		// several references, so there is no single value to order by.
		{
			key: 'platforms',
			header: 'Custodians',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (row) => row.refs?.length ?? 0
		},
		{
			key: 'enabled',
			header: 'Offered',
			align: 'center',
			// NOT `align: 'right'`. That sets `justify-content: flex-end` on the cell, which pins the
			// switch to the right edge so the row can never give the column its space back — the
			// table then scrolls horizontally with the panel half empty (the exact trap ca4bd7b
			// fixed on the transfer-providers table). Fixed width + centred, as there.
			sortable: true,
			sortValue: (row) => Number(row.enabled),
			headerClass: 'w-24 text-center',
			cellClass: 'w-24 text-center'
		},
		{ key: 'settings', header: '', align: 'right', headerClass: 'w-12', cellClass: 'w-12' }
	];

	let editing = $state<Row | null>(null);
	let allowedCustodies = $state<string[]>([]);
	let allowedClientTypes = $state<string[]>([]);
	let allowedCurrencies = $state<string[]>([]);
	let maxParties = $state(2);
	let minAge = $state<number | string>('');
	let maxAge = $state<number | string>('');
	let bankDetailsAllowed = $state(true);
	let seiTemplateId = $state('');
	const isPersonOnly = (productType: string): boolean => ['ISA', 'JISA'].includes(productType);

	const CUSTODY_OPTIONS: MultiSelectOption[] = [
		{ value: 'onshore', label: 'Onshore' },
		{ value: 'offshore', label: 'Offshore' }
	];
	const CLIENT_TYPE_OPTIONS: MultiSelectOption[] = $derived([
		{ value: 'individual', label: 'Individuals' },
		{
			value: 'organisation',
			label: 'Organisations',
			disabled: editing ? isPersonOnly(editing.product_type) : false
		}
	]);
	const currencyOptions: MultiSelectOption[] = $derived(
		currencies.map((currency) => ({
			value: currency.code,
			label: currency.currency ? `${currency.currency} (${currency.code})` : currency.code,
			hint: currency.symbol ?? undefined
		}))
	);

	function edit(row: Row): void {
		editing = row;
		allowedCustodies = [...row.allowed_custodies];
		allowedClientTypes = isPersonOnly(row.product_type)
			? row.allowed_client_types.filter((type) => type === 'individual')
			: [...row.allowed_client_types];
		allowedCurrencies = [...row.allowed_currencies];
		maxParties = row.max_parties;
		minAge = row.min_age ?? '';
		maxAge = row.max_age ?? '';
		bankDetailsAllowed = row.bank_details_allowed;
		const seiRef = row.refs.find((ref) => ref.provider === 'sei');
		seiTemplateId = seiRef?.account_open_reference || '__unassigned__';
	}

	const personOnly = $derived(editing ? isPersonOnly(editing.product_type) : false);
	const gbpOnly = $derived(editing ? ['ISA', 'JISA'].includes(editing.product_type) : false);
	const custodyPayload = $derived(JSON.stringify(allowedCustodies));
	const clientTypePayload = $derived(
		JSON.stringify(
			personOnly ? allowedClientTypes.filter((type) => type === 'individual') : allowedClientTypes
		)
	);
	const currencyPayload = $derived(JSON.stringify(allowedCurrencies));

	const templateOptions = $derived.by(() => {
		const list = [...seiAccountTemplates];
		if (
			seiTemplateId &&
			seiTemplateId !== '__unassigned__' &&
			!list.some((option) => option.value === seiTemplateId)
		) {
			list.unshift({
				value: seiTemplateId,
				label: seiTemplateId,
				hint: "Not in this firm's current templates",
				parent: seiFirmId
			});
		}
		return list;
	});
	const selectedTemplateName = $derived(
		templateOptions.find((option) => option.value === seiTemplateId)?.label
	);

	// Bits' onCheckedChange hands over the new value and nothing else — no event to walk up from
	// — so each row's form is found by id.
	function submitRow(productType: string): void {
		const el = document.getElementById(`product-${productType}`);
		if (el instanceof HTMLFormElement) el.requestSubmit();
	}

	$effect(() => {
		if (form?.saved === 'product') {
			toast.success('Product updated');
			editing = null;
		} else if (form?.error) toast.error(form.error);
	});
</script>

<div class="space-y-4">
	<div>
		<h2 class="text-lg font-semibold">Products</h2>
		<p class="text-muted-foreground text-sm">
			The wrappers this firm offers. Each custodian's own reference is shown as it will be sent to
			their API — reference data, set by what the custodian supports, not by us. The exception is
			the SEI enrolment template: which of this firm's templates opens this wrapper is a choice, set
			in the product's settings.
		</p>
	</div>

	<Card glass>
		<CardContent>
			<DataTable {columns} {rows} bind:sort getKey={(row) => row.product_type}>
				{#snippet cell(row, column)}
					{#if column.key === 'name'}
						<!-- A firm renames its own shelf, so this is authored text with no length we control.
						     (Discovery seeds it too, which is how a product once read "TPONBOND".) -->
						<TruncatedText text={row.name} class="{CELL_NAME} font-medium" />
					{:else if column.key === 'code'}
						<span class="font-mono text-xs">{row.code}</span>
					{:else if column.key === 'platforms'}
						<!-- One chip per platform that knows this wrapper. Absent rather than "Not linked":
						     a platform that does not offer a product has nothing to say about it, and a row
						     of empty slots read as work outstanding when there was none. -->
						<div class="flex min-w-0 flex-wrap items-center gap-1.5">
							{#each row.refs as ref (ref.provider)}
								{@const providerName =
									platforms.find((p) => p.provider === ref.provider)?.name ?? ref.provider}
								<!-- WHAT EACH ONE DOES, not just that it knows the wrapper (0122). The
								     estate's own SIPP is custodied at SEI and administered by Morgan
								     Lloyd, and until the role was shown the shelf listed both as
								     interchangeable "custodians" — which is the reading that sends an
								     instruction to the wrong provider. A mapping with no role stated
								     shows the platform alone rather than guessing at one. -->
								<span
									class="bg-muted/50 inline-flex items-baseline gap-1.5 rounded px-2 py-0.5 text-xs"
									title="{providerName}{ref.role
										? ` (${ROLE_LABELS[ref.role]})`
										: ''} calls it {ref.external_id ??
										ref.external_name ??
										'—'}{ref.account_open_reference
										? `; opens with ${ref.account_open_reference}`
										: ''}"
								>
									<span class="text-muted-foreground">{providerName}</span>
									{#if ref.role}
										<span class="text-muted-foreground/70">{ROLE_LABELS[ref.role]}</span>
									{/if}
									<span class="font-mono">{ref.external_id ?? ref.external_name ?? '—'}</span>
								</span>
							{:else}
								<span class="text-muted-foreground text-xs">
									{platforms.length ? 'No platform offers this' : 'No platforms connected'}
								</span>
							{/each}
						</div>
					{:else if column.key === 'enabled'}
						<!-- The switch IS the edit. Toggling a type the firm has no record for creates it:
						     the row exists because the vocabulary does, the record carries the decision. -->
						<form
							id="product-{row.product_type}"
							method="POST"
							action="?/product"
							use:enhance
							class="flex justify-center"
						>
							{#if row.product}<input type="hidden" name="uid" value={row.product.uid} />{/if}
							<input type="hidden" name="name" value={row.name} />
							<input type="hidden" name="product_type" value={row.product_type} />
							{#if row.code}<input type="hidden" name="code" value={row.code} />{/if}
							<input type="hidden" name="enabled" value={row.enabled ? '' : 'on'} />
							<Switch
								checked={row.enabled}
								disabled={!canManage}
								aria-label="Offer {row.name}"
								onCheckedChange={() => submitRow(row.product_type)}
							/>
						</form>
					{:else if column.key === 'settings'}
						<Button
							variant="ghost"
							size="icon"
							disabled={!canManage}
							aria-label="Configure {row.name}"
							onclick={() => edit(row)}
						>
							<SettingsIcon class="size-4" />
						</Button>
					{/if}
				{/snippet}
			</DataTable>
		</CardContent>
	</Card>
</div>

<Sheet.Root open={editing !== null} onOpenChange={(open) => !open && (editing = null)}>
	<Sheet.Content side="right" class="w-full">
		{#if editing}
			<Sheet.Header>
				<Sheet.Title>{editing.name}</Sheet.Title>
				<Sheet.Description>
					Set the choices and party limit enforced when this product is added to a journey.
				</Sheet.Description>
			</Sheet.Header>
			<form method="POST" action="?/product" use:enhance class="space-y-6 px-4 pb-6">
				{#if editing.product}
					<input type="hidden" name="uid" value={editing.product.uid} />
				{/if}
				<input type="hidden" name="name" value={editing.name} />
				<input type="hidden" name="product_type" value={editing.product_type} />
				<input type="hidden" name="allowed_custodies" value={custodyPayload} />
				<input type="hidden" name="allowed_client_types" value={clientTypePayload} />
				<input type="hidden" name="allowed_currencies" value={currencyPayload} />

				<div class="space-y-2">
					<Label for="product-custody">Available custody locations</Label>
					<!-- `portal={false}`: this form lives in a Sheet, and a portalled Popover sits
					     outside the sheet's focus trap so a click never lands (the vulnerability
					     characteristics picker already carries this). -->
					<MultiSelect
						id="product-custody"
						bind:value={allowedCustodies}
						options={CUSTODY_OPTIONS}
						placeholder="Choose custody locations…"
						searchPlaceholder="Search…"
						emptyText="No matches."
						portal={false}
					/>
					{#if allowedCustodies.length === 0}
						<p class="text-destructive text-sm">Choose at least one custody location.</p>
					{/if}
				</div>

				<div class="space-y-2">
					<Label for="product-owners">Eligible account owners</Label>
					<MultiSelect
						id="product-owners"
						bind:value={allowedClientTypes}
						options={CLIENT_TYPE_OPTIONS}
						placeholder="Choose account owners…"
						searchPlaceholder="Search…"
						emptyText="No matches."
						portal={false}
					/>
					{#if personOnly}
						<p class="text-muted-foreground text-xs">
							{editing.product_type} accounts can only be owned by individuals.
						</p>
					{:else if allowedClientTypes.length === 0}
						<p class="text-destructive text-sm">Choose at least one client type.</p>
					{/if}
				</div>

				<div class="space-y-2">
					<Label for="product-currencies">Account currencies</Label>
					<MultiSelect
						id="product-currencies"
						bind:value={allowedCurrencies}
						options={currencyOptions}
						placeholder="Choose currencies…"
						searchPlaceholder="Search currencies…"
						emptyText="No currencies found."
						disabled={gbpOnly}
						portal={false}
					/>
					{#if gbpOnly}
						<p class="text-muted-foreground text-xs">
							{editing.product_type} accounts are restricted to GBP.
						</p>
					{:else if allowedCurrencies.length === 0}
						<p class="text-destructive text-sm">Choose at least one currency.</p>
					{/if}
				</div>

				<div class="space-y-2">
					<Label for="product-max-parties">Maximum parties per account</Label>
					<Input
						id="product-max-parties"
						name="max_parties"
						type="number"
						min="1"
						bind:value={maxParties}
					/>
				</div>

				<div class="grid grid-cols-2 gap-3">
					<div class="space-y-2">
						<Label for="product-min-age">Minimum owner age</Label>
						<Input
							id="product-min-age"
							name="min_age"
							type="number"
							min="0"
							bind:value={minAge}
							placeholder="None"
						/>
					</div>
					<div class="space-y-2">
						<Label for="product-max-age">Maximum owner age</Label>
						<Input
							id="product-max-age"
							name="max_age"
							type="number"
							min="0"
							bind:value={maxAge}
							placeholder="None"
						/>
						<p class="text-muted-foreground text-xs">
							Completed years on the journey start date. A JISA owner must be 17 or under.
						</p>
					</div>
				</div>

				<div class="space-y-2">
					<label class="flex items-center gap-3 text-sm">
						<Checkbox
							name="bank_details_allowed"
							checked={bankDetailsAllowed}
							onCheckedChange={(value) => (bankDetailsAllowed = value)}
						/>
						Allow withdrawal bank details
					</label>
					<p class="text-muted-foreground text-xs">
						Turn this off when money cannot be withdrawn directly from this product, such as a JISA.
					</p>
				</div>

				<div class="space-y-4">
					<div>
						<h3 class="text-sm font-medium">SEI</h3>
						<p class="text-muted-foreground text-xs">
							The enrolment template this wrapper opens with at SEI. Only this tenant's enabled firm
							is listed — the firm is set on the platform connection.
						</p>
					</div>
					{#if seiFirmId}
						{#if editing?.refs.some((ref) => ref.provider === 'sei')}
							<input type="hidden" name="sei_mapped" value="true" />
						{/if}
						<input
							type="hidden"
							name="account_open_reference"
							value={seiTemplateId === '__unassigned__' ? '' : seiTemplateId}
						/>
						<div class="space-y-1.5">
							<Label class="text-muted-foreground text-xs" for="product-sei-template">
								Enrolment template
							</Label>
							<Select.Root type="single" bind:value={seiTemplateId}>
								<Select.Trigger id="product-sei-template" class="w-full">
									{selectedTemplateName ??
										(seiTemplateId && seiTemplateId !== '__unassigned__'
											? seiTemplateId
											: 'Select a template…')}
								</Select.Trigger>
								<!-- `portalProps.disabled`: this form lives in a Sheet, and a portalled
								     menu sits outside the sheet's focus trap so a click never lands. -->
								<Select.Content portalProps={{ disabled: true }}>
									<Select.Item value="__unassigned__" label="Not assigned" />
									{#each templateOptions as option (option.value)}
										<Select.Item value={option.value} label={option.label} />
									{/each}
								</Select.Content>
							</Select.Root>
							<p class="text-muted-foreground text-xs">
								{#if !seiAccountTemplates.length}
									No account templates in the warehouse for this firm yet — wait for
									<code>ref.enrollment_templates</code> to sync.
								{:else}
									Required to open an account at SEI. One template per wrapper.
								{/if}
							</p>
						</div>
					{:else}
						<p class="text-muted-foreground text-xs">
							Enable SEI on this tenant to assign an enrolment template. The firm comes from that
							connection; it is not chosen per product.
						</p>
					{/if}
				</div>

				<div class="flex justify-end gap-2">
					<Button type="button" variant="outline" onclick={() => (editing = null)}>Cancel</Button>
					<Button
						type="submit"
						disabled={allowedCustodies.length === 0 ||
							allowedClientTypes.length === 0 ||
							allowedCurrencies.length === 0}
					>
						Save product settings
					</Button>
				</div>
			</form>
		{/if}
	</Sheet.Content>
</Sheet.Root>
