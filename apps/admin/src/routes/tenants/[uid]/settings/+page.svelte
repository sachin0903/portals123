<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		buttonVariants,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		Input,
		Label,
		MultiSelect,
		Select,
		Switch,
		toast,
		type MultiSelectOption
	} from '@graphene/ui';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const tenant = $derived(data.tenant);

	// Switching a firm off is the incident kill switch — confirm before letting the toggle land.
	let enabled = $state(untrack(() => data.tenant.enabled));
	let disableOpen = $state(false);
	function onEnabledChange(next: boolean) {
		if (!next) {
			enabled = true;
			disableOpen = true;
		}
	}

	let saving = $state(false);

	const currencyOptions: MultiSelectOption[] = $derived(
		data.currencySettings.currencies.map((currency) => ({
			value: currency.code,
			label: currency.currency ? `${currency.currency} (${currency.code})` : currency.code,
			hint: currency.symbol ?? undefined
		}))
	);
	let allowedCurrencies = $state<string[]>(
		untrack(() => [...data.currencySettings.allowed_currencies])
	);
	let baseCurrency = $state(untrack(() => data.currencySettings.base_currency));
	const allowedCurrencyOptions = $derived(
		currencyOptions.filter((option) => allowedCurrencies.includes(option.value))
	);
	const baseCurrencyLabel = $derived(
		allowedCurrencyOptions.find((option) => option.value === baseCurrency)?.label ??
			'Select a base currency…'
	);
	let savingCurrencies = $state(false);
	$effect(() => {
		if (!allowedCurrencies.includes(baseCurrency)) baseCurrency = allowedCurrencies[0] ?? '';
	});

	// Deletion: type the alias, then confirm. Both halves are re-checked by the API.
	let deleteConfirm = $state('');
	let deleteOpen = $state(false);
	let deleting = $state(false);
	let deleteForm = $state<HTMLFormElement | undefined>();
	const deleteArmed = $derived(deleteConfirm.trim() === tenant.alias);
</script>

<!-- The heading is the Settings layout's; this panel is its General section. -->
<div class="max-w-xl space-y-6">
	<Card glass>
		<CardHeader>
			<CardTitle>Firm settings</CardTitle>
			<CardDescription>Name, alias, website and whether the firm can sign in.</CardDescription>
		</CardHeader>
		<CardContent>
			<form
				method="POST"
				action="?/editTenant"
				use:enhance={() => {
					saving = true;
					return async ({ result, update }) => {
						saving = false;
						if (result.type === 'success') {
							const revoked = result.data?.sessionsRevoked as number | null | undefined;
							toast.success(
								typeof revoked === 'number'
									? `Firm suspended. ${revoked} active session(s) ended.`
									: 'Settings saved.'
							);
							await update({ reset: false });
						} else {
							toast.error(
								result.type === 'failure'
									? String(result.data?.error ?? 'Something went wrong.')
									: 'Something went wrong.'
							);
							await update();
						}
					};
				}}
				class="space-y-4"
			>
				<div class="space-y-2">
					<Label for="name">Name</Label>
					<Input id="name" name="name" value={tenant.name} disabled={!data.canManage} />
				</div>
				<div class="space-y-2">
					<Label for="alias">Alias</Label>
					<Input
						id="alias"
						name="alias"
						value={tenant.alias}
						pattern="[a-z0-9-]+"
						disabled={!data.canManage}
					/>
					<p class="text-muted-foreground text-xs">
						Use lowercase letters, digits and hyphens. This internal identifier is not a host name.
					</p>
				</div>
				<div class="space-y-2">
					<Label for="website">Website</Label>
					<Input
						id="website"
						name="website"
						value={tenant.website ?? ''}
						placeholder="https://…"
						disabled={!data.canManage}
					/>
				</div>
				<div class="flex items-center gap-2">
					<Switch
						id="enabled"
						name="enabled"
						bind:checked={enabled}
						onCheckedChange={onEnabledChange}
						disabled={!data.canManage}
					/>
					<Label for="enabled" class="font-normal">
						Enabled. When off, the firm's people cannot sign in.
					</Label>
				</div>
				{#if data.canManage}
					<Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</Button>
				{/if}
			</form>
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Currencies</CardTitle>
			<CardDescription>
				Choose which currencies this firm may use and the base currency selected for new accounts.
			</CardDescription>
		</CardHeader>
		<CardContent>
			<form
				method="POST"
				action="?/setCurrencies"
				use:enhance={() => {
					savingCurrencies = true;
					return async ({ result, update }) => {
						savingCurrencies = false;
						if (result.type === 'success') toast.success('Currency settings saved.');
						else {
							toast.error(
								result.type === 'failure'
									? String(result.data?.error ?? 'Something went wrong.')
									: 'Something went wrong.'
							);
						}
						await update({ reset: false });
					};
				}}
				class="space-y-4"
			>
				<div class="space-y-2">
					<Label for="allowed-currencies">Allowed currencies</Label>
					<MultiSelect
						id="allowed-currencies"
						name="allowed_currencies"
						bind:value={allowedCurrencies}
						options={currencyOptions}
						placeholder="Choose currencies…"
						searchPlaceholder="Search currencies…"
						emptyText="No currencies found."
						disabled={!data.canManage}
					/>
					<p class="text-muted-foreground text-xs">
						Only these currencies are offered when a new account’s currency is chosen.
					</p>
				</div>
				<div class="space-y-2">
					<Label for="base-currency">Base currency</Label>
					<input type="hidden" name="base_currency" value={baseCurrency} />
					<Select.Root type="single" bind:value={baseCurrency} disabled={!data.canManage}>
						<Select.Trigger id="base-currency" class="w-full">
							{baseCurrencyLabel}
						</Select.Trigger>
						<Select.Content>
							{#each allowedCurrencyOptions as option (option.value)}
								<Select.Item value={option.value} label={option.label} />
							{/each}
						</Select.Content>
					</Select.Root>
					<p class="text-muted-foreground text-xs">
						Used as the default for new accounts. It must remain in the allowed list.
					</p>
				</div>
				{#if data.canManage}
					<Button
						type="submit"
						disabled={savingCurrencies || !baseCurrency || allowedCurrencies.length === 0}
					>
						{savingCurrencies ? 'Saving…' : 'Save currencies'}
					</Button>
				{/if}
			</form>
		</CardContent>
	</Card>

	<!-- Offboarding. Deliberately ordered: suspend (above), export, delete. -->
	<Card glass>
		<CardHeader>
			<CardTitle>Offboarding</CardTitle>
			<CardDescription>
				Taking a firm off the platform: suspend it first, take its archive, then delete.
			</CardDescription>
		</CardHeader>
		<CardContent class="space-y-4">
			<div class="flex flex-wrap items-center justify-between gap-3">
				<div class="min-w-0">
					<p class="text-sm font-medium">Download archive</p>
					<p class="text-muted-foreground text-xs">
						The firm's identity, roles, members and full audit trail, plus row counts for its data.
						Deleting the firm removes its audit trail, so keep this for record-keeping.
					</p>
				</div>
				{#if data.canManage}
					<a
						href="/tenants/{tenant.uid}/export"
						class={buttonVariants({ variant: 'secondary', size: 'sm' })}
						download
					>
						<DownloadIcon class="size-4" /> Export
					</a>
				{/if}
			</div>

			{#if data.canManage}
				<div class="border-destructive/40 space-y-3 rounded-lg border border-dashed p-3">
					<Alert variant="destructive">
						<TriangleAlertIcon class="size-4" />
						<AlertTitle>Deleting a firm cannot be undone</AlertTitle>
						<AlertDescription>
							Its logins, WorkOS memberships, configuration and data go with it, including its audit
							trail. The firm must be suspended first. Its WorkOS project itself is deleted by hand
							in the WorkOS dashboard.
						</AlertDescription>
					</Alert>
					<form
						method="POST"
						action="?/deleteTenant"
						bind:this={deleteForm}
						use:enhance={() => {
							deleting = true;
							return async ({ result, update }) => {
								deleting = false;
								if (result.type === 'redirect') {
									toast.success(`${tenant.name} deleted.`);
								} else if (result.type === 'failure') {
									toast.error(String(result.data?.error ?? 'Something went wrong.'));
								}
								await update();
							};
						}}
						class="space-y-2"
					>
						<Label for="confirm"
							>Type <span class="font-mono">{tenant.alias}</span> to confirm</Label
						>
						<div class="flex items-center gap-2">
							<Input
								id="confirm"
								name="confirm"
								bind:value={deleteConfirm}
								placeholder={tenant.alias}
								autocomplete="off"
								class="max-w-xs"
							/>
							<Button
								type="button"
								variant="destructive"
								disabled={!deleteArmed || tenant.enabled || deleting}
								onclick={() => (deleteOpen = true)}
							>
								{deleting ? 'Deleting…' : 'Delete firm'}
							</Button>
						</div>
						{#if tenant.enabled}
							<p class="text-muted-foreground text-xs">
								Suspend the firm above (and save) before it can be deleted.
							</p>
						{/if}
					</form>
				</div>
			{/if}
		</CardContent>
	</Card>
</div>

<ConfirmDialog
	bind:open={disableOpen}
	title="Suspend sign-in for this firm?"
	description="Every member of {tenant.name} is signed out and blocked from both portals. Re-enabling restores access."
	confirmLabel="Suspend"
	destructive
	onconfirm={() => {
		enabled = false;
	}}
/>

<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete {tenant.name}?"
	description="This permanently removes the firm, its logins, its configuration, its data and its audit trail. Download the archive first if you have not already."
	confirmLabel="Delete permanently"
	destructive
	onconfirm={() => deleteForm?.requestSubmit()}
/>
