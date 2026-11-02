<script lang="ts">
	// One platform's enablement for this firm: an enable switch top-right, and —
	// once on — the provider-shaped slice filter. Saving PUTs the enablement and fans
	// the firm's slice out into its lake immediately.
	//
	// The pickers are driven by `options`, read live from the core warehouse
	// (/admin/providers/{p}/options) rather than the hand-registered
	// provider_sources: a firm's SEI subfirms and DSR teams change whenever SEI
	// reorganises, and there are 500+ DSR teams. Where a provider has no options yet
	// (its models have never run) the card falls back to manual id entry.
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		Input,
		Label,
		MultiSelect,
		Select,
		Switch,
		type MultiSelectOption
	} from '@graphene/ui';
	import type {
		ProviderOption,
		ProviderOptions,
		ProviderSource,
		TenantProvider
	} from './+page.server';
	import type { ProviderMeta } from '$lib/platforms/registry';
	import {
		CUSTODY_KEYS,
		hydrateCustodyOrgs,
		UNASSIGNED_ORG,
		type Custody
	} from '$lib/platforms/custody-orgs';

	let {
		provider,
		meta,
		sources,
		options,
		enablement,
		canWrite = true
	}: {
		/** The provider CODE (`morgan-lloyd`), which is what the enablement API is addressed by —
		 *  and, since 0138, the URL segment too: there is one spelling. */
		provider: string;
		/** How it presents — from the API's registry, not a table in this app. */
		meta: ProviderMeta;
		sources: ProviderSource[];
		options: ProviderOptions;
		enablement: TenantProvider | null;
		/** tenants:write — without it the card is read-only: state visible, controls disabled
		 *  (the form action and the API refuse the write regardless; this keeps the UI honest). */
		canWrite?: boolean;
	} = $props();

	const configList = (key: string): string[] => {
		const value = (enablement?.config ?? {})[key];
		if (Array.isArray(value)) return value.map(String);
		return value ? [String(value)] : [];
	};

	const configOrg = (key: Custody): string => {
		const raw = (enablement?.config ?? {})['custody_orgs'];
		if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
			const id = (raw as Record<string, unknown>)[key];
			return typeof id === 'string' ? id : '';
		}
		return '';
	};

	const configCustodyDsrs = (key: Custody): string[] => {
		const raw = (enablement?.config ?? {})['custody_dsrs'];
		if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
			const value = (raw as Record<string, unknown>)[key];
			if (Array.isArray(value)) return value.map(String);
		}
		return [];
	};

	const custodyDsrState = (): Record<Custody, string[]> =>
		Object.fromEntries(CUSTODY_KEYS.map((key) => [key, configCustodyDsrs(key)])) as Record<
			Custody,
			string[]
		>;
	const custodyHeading = (key: Custody): string =>
		key === 'onshore' ? 'Onshore custody' : 'Offshore custody';

	const custodyBlank = (key: Custody): string =>
		key === 'onshore' ? 'Onshore (default)' : 'Not assigned';

	// Switch state: on = show/edit the config. Turning OFF an enabled provider
	// disables it immediately (config kept); turning ON only reveals the editor —
	// enabling happens on save, once a valid filter is picked.
	// (Initial-value capture is deliberate: the effect below resyncs on reload.)
	// svelte-ignore state_referenced_locally
	let on = $state(enablement?.enabled ?? false);
	let firmId = $state(configList('firm_id')[0] ?? '');
	let individualTemplateId = $state(
		configList('client_template_individual_id')[0] ?? configList('client_template_id')[0] ?? ''
	);
	let organisationTemplateId = $state(configList('client_template_organisation_id')[0] ?? '');
	let dsrIds = $state<string[]>(configList('dsr_ids'));
	let custodyOrgs = $state<Partial<Record<Custody, string>>>({
		onshore: configOrg('onshore') || undefined,
		offshore: configOrg('offshore') || undefined
	});
	let custodyDsrs = $state<Record<Custody, string[]>>(custodyDsrState());
	// The custodian's collection bank account (where clients pay money in). Shown on the
	// account's payment drawer. SEI publishes no per-firm pay-in account, so it is firm config.
	let payInAccountName = $state(configList('pay_in_account_name')[0] ?? '');
	let payInSortCode = $state(configList('pay_in_sort_code')[0] ?? '');
	let payInAccountNumber = $state(configList('pay_in_account_number')[0] ?? '');
	// `def` identifies the card and never changes for a given instance, so capturing
	// it here is correct; the effect below resyncs the value on reload.
	// svelte-ignore state_referenced_locally
	let nodeIds = $state<string[]>(configList(provider === 'seccl' ? 'node_ids' : 'tenant_ids'));
	let manual = $state('');
	let disableForm: HTMLFormElement;

	/** API options carry `hint: string | null`; the component wants `hint?: string`. */
	const toPicker = (rows: ProviderOption[] = []): MultiSelectOption[] =>
		rows.map(({ value, label, hint }) => ({ value, label, hint: hint ?? undefined }));

	$effect(() => {
		on = enablement?.enabled ?? false;
		firmId = configList('firm_id')[0] ?? '';
		individualTemplateId =
			configList('client_template_individual_id')[0] ?? configList('client_template_id')[0] ?? '';
		organisationTemplateId = configList('client_template_organisation_id')[0] ?? '';
		dsrIds = configList('dsr_ids');
		custodyDsrs = custodyDsrState();
		payInAccountName = configList('pay_in_account_name')[0] ?? '';
		payInSortCode = configList('pay_in_sort_code')[0] ?? '';
		payInAccountNumber = configList('pay_in_account_number')[0] ?? '';
		nodeIds = configList(provider === 'seccl' ? 'node_ids' : 'tenant_ids');
		const firm = configList('firm_id')[0] ?? '';
		const rows = (options.subfirms ?? []).filter(
			(row) => !firm || row.parent === firm || !row.parent
		);
		custodyOrgs = hydrateCustodyOrgs(
			{ onshore: configOrg('onshore'), offshore: configOrg('offshore') },
			toPicker(rows),
			configList('subfirm_ids')
		);
	});

	const toggle = (checked: boolean) => {
		if (!checked && enablement?.enabled) disableForm.requestSubmit();
		on = checked;
	};
	const fromSources = (): MultiSelectOption[] =>
		sources.map((s) => ({ value: s.external_id, label: s.name, hint: s.external_id }));

	// SEI narrows firm → DSR / custody, so those lists only offer children of the
	// chosen firm. Options carry `parent` (the owning firm) for exactly this. Rows with
	// no parent are kept — an unattributed id is better shown than silently hidden.
	const byFirm = (rows: ProviderOption[] = []): ProviderOption[] =>
		firmId ? rows.filter((r) => r.parent === firmId || !r.parent) : rows;

	const firmOptions = $derived.by(() => {
		const rows = options.firms?.length ? toPicker(options.firms) : fromSources();
		if (firmId && !rows.some((row) => row.value === firmId)) {
			return [...rows, { value: firmId, label: firmId }];
		}
		return rows;
	});
	const subfirmOptions = $derived(toPicker(byFirm(options.subfirms)));
	const dsrOptions = $derived(toPicker(byFirm(options.dsr_teams)));
	const clientTemplateOptions = $derived(toPicker(byFirm(options.client_templates)));
	// Seccl: one flat list of the firm's nodes. Morgan Lloyd has no live source, so it
	// keeps using the registered provider_sources.
	const nodeOptions = $derived(provider === 'seccl' ? toPicker(options.nodes) : fromSources());

	const firmName = $derived(firmOptions.find((o) => o.value === firmId)?.label);
	const individualTemplateName = $derived(
		clientTemplateOptions.find((option) => option.value === individualTemplateId)?.label
	);
	const organisationTemplateName = $derived(
		clientTemplateOptions.find((option) => option.value === organisationTemplateId)?.label
	);

	$effect(() => {
		if (!clientTemplateOptions.length) return;
		const known = new Set(clientTemplateOptions.map((option) => option.value));
		if (individualTemplateId && !known.has(individualTemplateId)) individualTemplateId = '';
		if (organisationTemplateId && !known.has(organisationTemplateId)) organisationTemplateId = '';
	});
	$effect(() => {
		const allowed = new Set(dsrOptions.map((o) => o.value));
		if (dsrIds.some((id) => !allowed.has(id))) {
			dsrIds = dsrIds.filter((id) => allowed.has(id));
		}
		const next = { ...custodyDsrs };
		let changed = false;
		for (const key of CUSTODY_KEYS) {
			const kept = custodyDsrs[key].filter((id) => allowed.has(id));
			if (kept.length !== custodyDsrs[key].length) {
				next[key] = kept;
				changed = true;
			}
		}
		if (changed) custodyDsrs = next;
	});
	$effect(() => {
		if (!subfirmOptions.length) return;
		const allowed = new Set(subfirmOptions.map((o) => o.value));
		const next: Partial<Record<Custody, string>> = { ...custodyOrgs };
		let changed = false;
		for (const key of CUSTODY_KEYS) {
			if (next[key] && !allowed.has(next[key]!)) {
				delete next[key];
				changed = true;
			}
		}
		if (changed) custodyOrgs = next;
	});
	const orgValue = (key: Custody): string => custodyOrgs[key] || UNASSIGNED_ORG;
	const orgName = (id: string | undefined): string =>
		subfirmOptions.find((option) => option.value === id)?.label ?? id ?? '';
</script>

<Card glass class="flex flex-col">
	<CardHeader class="gap-1">
		<div class="flex items-start justify-between gap-3">
			<div>
				<CardTitle class="text-base">{meta.name}</CardTitle>
				<CardDescription>{meta.description}</CardDescription>
			</div>
			<div class="flex shrink-0 items-center gap-2 pt-0.5">
				{#if enablement?.enabled}
					<Badge variant="secondary">enabled</Badge>
				{/if}
				<Switch
					checked={on}
					onCheckedChange={toggle}
					disabled={!canWrite}
					aria-label="Enable {meta.name}"
				/>
			</div>
		</div>
	</CardHeader>
	<CardContent class="flex flex-1 flex-col gap-3">
		{#if on}
			<form method="POST" action="?/setProvider" use:enhance class="flex flex-1 flex-col gap-3">
				<input type="hidden" name="provider" value={provider} />
				<input type="hidden" name="enabled" value="true" />
				{#if provider === 'sei'}
					<input type="hidden" name="firm_id" value={firmId} />
					<div class="space-y-1.5">
						<Label class="text-muted-foreground text-xs" for="{provider}-firm">Firm</Label>
						<Select.Root type="single" bind:value={firmId}>
							<Select.Trigger id="{provider}-firm" class="w-full">
								{firmName ?? (firmId || 'Select a firm…')}
							</Select.Trigger>
							<Select.Content>
								{#each firmOptions as option (option.value)}
									<Select.Item value={option.value} label={option.label} />
								{/each}
							</Select.Content>
						</Select.Root>
					</div>
					<div class="grid grid-cols-2 gap-3">
						<div class="space-y-1.5">
							<Label
								class="text-muted-foreground text-xs"
								for="{provider}-client-template-individual"
							>
								Individual client template
							</Label>
							<input
								type="hidden"
								name="client_template_individual_id"
								value={individualTemplateId}
							/>
							<Select.Root
								type="single"
								value={individualTemplateId || UNASSIGNED_ORG}
								onValueChange={(value) => {
									individualTemplateId = !value || value === UNASSIGNED_ORG ? '' : value;
								}}
							>
								<Select.Trigger
									id="{provider}-client-template-individual"
									class="w-full"
									disabled={!firmId}
								>
									{individualTemplateName ??
										(individualTemplateId ||
											(firmId ? 'Select a template…' : 'Select a firm first'))}
								</Select.Trigger>
								<Select.Content>
									<Select.Item value={UNASSIGNED_ORG} label="Not assigned" />
									{#each clientTemplateOptions as option (option.value)}
										<Select.Item value={option.value} label={option.label} />
									{/each}
								</Select.Content>
							</Select.Root>
						</div>
						<div class="space-y-1.5">
							<Label
								class="text-muted-foreground text-xs"
								for="{provider}-client-template-organisation"
							>
								Organisation client template
							</Label>
							<input
								type="hidden"
								name="client_template_organisation_id"
								value={organisationTemplateId}
							/>
							<Select.Root
								type="single"
								value={organisationTemplateId || UNASSIGNED_ORG}
								onValueChange={(value) => {
									organisationTemplateId = !value || value === UNASSIGNED_ORG ? '' : value;
								}}
							>
								<Select.Trigger
									id="{provider}-client-template-organisation"
									class="w-full"
									disabled={!firmId}
								>
									{organisationTemplateName ??
										(organisationTemplateId ||
											(firmId ? 'Select a template…' : 'Select a firm first'))}
								</Select.Trigger>
								<Select.Content>
									<Select.Item value={UNASSIGNED_ORG} label="Not assigned" />
									{#each clientTemplateOptions as option (`org-${option.value}`)}
										<Select.Item value={option.value} label={option.label} />
									{/each}
								</Select.Content>
							</Select.Root>
						</div>
					</div>
					<div class="space-y-1.5">
						<Label class="text-muted-foreground text-xs" for="{provider}-dsrs">DSR teams</Label>
						<MultiSelect
							id="{provider}-dsrs"
							name="dsr_ids"
							bind:value={dsrIds}
							options={dsrOptions}
							disabled={!firmId}
							placeholder={firmId ? 'All DSR teams' : 'Select a firm first'}
							searchPlaceholder="Search DSR teams…"
							emptyText="No DSR teams for this firm."
						/>
						<p class="text-muted-foreground text-xs">
							Restricts the tenant to the DSRs specified, and which are applied to any new clients &
							accounts.
						</p>
					</div>
					<div class="space-y-1.5">
						<div class="grid grid-cols-2 gap-3">
							{#each CUSTODY_KEYS as key (key)}
								<div class="space-y-1">
									<Label class="text-muted-foreground text-xs" for="{provider}-org-{key}">
										{custodyHeading(key)}
									</Label>
									<input
										type="hidden"
										name="custody_org_{key}"
										value={custodyOrgs[key] && custodyOrgs[key] !== UNASSIGNED_ORG
											? custodyOrgs[key]
											: ''}
									/>
									<Select.Root
										type="single"
										value={orgValue(key)}
										onValueChange={(value) => {
											custodyOrgs = {
												...custodyOrgs,
												[key]: !value || value === UNASSIGNED_ORG ? undefined : value
											};
										}}
									>
										<Select.Trigger id="{provider}-org-{key}" class="w-full" disabled={!firmId}>
											{orgName(custodyOrgs[key]) || custodyBlank(key)}
										</Select.Trigger>
										<Select.Content>
											<Select.Item value={UNASSIGNED_ORG} label={custodyBlank(key)} />
											{#each subfirmOptions as option (option.value)}
												<Select.Item value={option.value} label={option.label} />
											{/each}
										</Select.Content>
									</Select.Root>
									<MultiSelect
										id="{provider}-dsr-{key}"
										name="custody_dsr_{key}"
										value={custodyDsrs[key]}
										onValueChange={(value) => {
											custodyDsrs = { ...custodyDsrs, [key]: value };
										}}
										options={dsrOptions}
										disabled={!firmId}
										placeholder="DSR teams (optional)"
										searchPlaceholder="Search DSR teams…"
										emptyText="No DSR teams for this firm."
									/>
								</div>
							{/each}
						</div>
						<p class="text-muted-foreground text-xs">
							Custody defined by sub firms, any DSRs specified are applied to new clients & accounts
							of that custody type.
						</p>
					</div>
					<div class="space-y-1.5">
						<Label class="text-muted-foreground text-xs" for="{provider}-pay-in-name">
							Custodian pay-in account
						</Label>
						<Input
							id="{provider}-pay-in-name"
							name="pay_in_account_name"
							bind:value={payInAccountName}
							placeholder="Account name"
							class="h-8 text-xs"
							disabled={!canWrite}
						/>
						<div class="grid grid-cols-2 gap-3">
							<Input
								name="pay_in_sort_code"
								bind:value={payInSortCode}
								placeholder="Sort code"
								class="h-8 font-mono text-xs"
								disabled={!canWrite}
							/>
							<Input
								name="pay_in_account_number"
								bind:value={payInAccountNumber}
								placeholder="Account number"
								class="h-8 font-mono text-xs"
								disabled={!canWrite}
							/>
						</div>
						<p class="text-muted-foreground text-xs">
							Where clients pay money in. Shown on the account's payment drawer, with the account's
							SEI number as the reference.
						</p>
					</div>
				{:else}
					<div class="space-y-1.5">
						<Label class="text-muted-foreground text-xs" for="{provider}-ids">
							{meta.source_label}
						</Label>
						{#if nodeOptions.length}
							<MultiSelect
								id="{provider}-ids"
								name="source"
								bind:value={nodeIds}
								options={nodeOptions}
								placeholder="Select…"
								searchPlaceholder="Search…"
							/>
						{:else}
							<Input
								id="{provider}-ids"
								name="manual"
								bind:value={manual}
								placeholder="Comma-separated ids"
								class="h-8 font-mono text-xs"
							/>
						{/if}
						{#if provider !== 'seccl'}
							<p class="text-muted-foreground text-xs">
								Sources are registered on the
								<a href="/platforms/{provider}" class="underline">{meta.name} platform page</a>.
							</p>
						{/if}
					</div>
				{/if}
				<div class="mt-auto flex items-center gap-3 pt-1">
					<Button type="submit" size="sm" disabled={!canWrite}>
						{enablement?.enabled ? 'Save & sync' : 'Enable & sync'}
					</Button>
					{#if enablement?.enabled}
						<!-- "last merged", not "last fan-out": the fan-out was a step in the deleted
						     three-hop path, and this column is stamped by the MERGE now
						     (`book_sink.merge`). The word survived the thing it named. -->
						<span class="text-muted-foreground text-xs">
							last merged: {enablement.last_fanout_at
								? new Date(enablement.last_fanout_at).toLocaleString('en-GB')
								: 'never'}
						</span>
					{/if}
				</div>
			</form>
			{#if enablement?.storage_missing}
				<!-- THE SILENT EXCLUSION. `book_sink.tenants` INNER JOINs an ACTIVE storage row, so a
				     firm without one registers no models on the daemon at all — it is enabled, green,
				     and invisible to the pipeline. The daemon has always known; nothing told the
				     operator. -->
				<Alert variant="destructive" class="mt-3">
					<AlertDescription>{enablement.storage_missing}</AlertDescription>
				</Alert>
			{/if}
			{#if enablement?.enabled}
				<!-- Refresh is its OWN form: it re-runs this firm's pipeline models on the daemon, and
				     must not carry the config editor's fields — a stray edit riding along with a re-run
				     is how a filter changes without anyone choosing to change it. -->
				<form method="POST" action="?/refreshProvider" use:enhance class="mt-3">
					<input type="hidden" name="provider" value={provider} />
					<div class="flex items-center gap-3">
						<Button type="submit" size="sm" variant="outline" disabled={!canWrite}>
							Refresh this firm
						</Button>
						<span class="text-muted-foreground text-xs">
							Refreshes {meta.name} data for this firm only.
						</span>
					</div>
				</form>
			{/if}
		{:else}
			<p class="text-muted-foreground my-auto text-sm">
				{meta.name} is not enabled for this firm.
			</p>
		{/if}
	</CardContent>
</Card>

<!-- Disable path: switch off => immediate PUT enabled=false, config preserved
     (the API validates config even on disable, so it rides along). -->
<form method="POST" action="?/setProvider" use:enhance bind:this={disableForm} hidden>
	<input type="hidden" name="provider" value={provider} />
	<input type="hidden" name="enabled" value="false" />
	{#if provider === 'sei'}
		<input type="hidden" name="firm_id" value={firmId} />
		<input type="hidden" name="client_template_individual_id" value={individualTemplateId} />
		<input type="hidden" name="client_template_organisation_id" value={organisationTemplateId} />
		<input type="hidden" name="pay_in_account_name" value={payInAccountName} />
		<input type="hidden" name="pay_in_sort_code" value={payInSortCode} />
		<input type="hidden" name="pay_in_account_number" value={payInAccountNumber} />
		{#each dsrIds as id (id)}
			<input type="hidden" name="dsr_ids" value={id} />
		{/each}
		{#each CUSTODY_KEYS as key (key)}
			<input type="hidden" name="custody_org_{key}" value={custodyOrgs[key] ?? ''} />
			{#each custodyDsrs[key] as id (`${key}-${id}`)}
				<input type="hidden" name="custody_dsr_{key}" value={id} />
			{/each}
		{/each}
	{:else}
		{#each nodeIds as id (id)}
			<input type="hidden" name="source" value={id} />
		{/each}
	{/if}
</form>
