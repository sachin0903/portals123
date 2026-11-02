<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Badge,
		Button,
		Card,
		CardAction,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		Input,
		Label,
		SelectMenu,
		Sheet,
		Switch,
		Textarea,
		toast
	} from '@graphene/ui';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import type { Product, Provider } from './types';

	let {
		kind,
		providers,
		products,
		canManage,
		form
	}: {
		kind: 'third_party' | 'transfer';
		providers: Provider[];
		products: Product[];
		canManage: boolean;
		form: { error?: string; saved?: string } | null;
	} = $props();

	const isTransfer = $derived(kind === 'transfer');
	// The firm's own shelf, offered as the wrapper vocabulary — a third-party provider's product
	// should be nameable in the same words the shelf uses, and "not stated" is a real answer for a
	// register row the custodian left blank.
	const wrapperOptions = $derived([
		{ value: '', label: 'Not stated' },
		...products.map((product) => ({
			value: product.product_type,
			label: `${product.name} (${product.product_type})`
		}))
	]);
	const meta = $derived(
		isTransfer
			? {
					title: 'Transfer providers',
					description:
						'Who a transfer cedes from or to. The masterparty and Origo ids are what a transfer instruction names.'
				}
			: {
					title: 'Third-party providers',
					description:
						'Providers whose products the firm can hold alongside its own, such as an external SIPP or offshore bond.'
				}
	);

	// One registry per page. They stay separate tables because a transfer provider carries the
	// ceding references a third-party product has no use for, and the same organisation
	// legitimately appears on both lists with different products enabled.
	let editing = $state<Provider | null>(null);
	let creating = $state(false);

	$effect(() => {
		if (form?.saved === 'provider') {
			toast.success('Provider saved');
			creating = false;
			editing = null;
		} else if (form?.error) {
			toast.error(form.error);
		}
	});
</script>

<Card glass>
	<CardHeader class="gap-4">
		<div>
			<CardTitle>{meta.title}</CardTitle>
			<CardDescription>{meta.description}</CardDescription>
		</div>
		{#if canManage}
			<CardAction>
				<Button onclick={() => (creating = true)}>
					<PlusIcon class="mr-1.5 size-4" /> New provider
				</Button>
			</CardAction>
		{/if}
	</CardHeader>
	<CardContent class="space-y-2">
		{#each providers as provider (provider.id)}
			<button
				type="button"
				class="hover:bg-muted/50 flex w-full items-start justify-between gap-3 rounded border px-3 py-2 text-left"
				onclick={() => (editing = provider)}
			>
				<div class="min-w-0">
					<span class="block truncate text-sm font-medium">{provider.name}</span>
					{#if provider.description}
						<span class="text-muted-foreground block truncate text-xs">{provider.description}</span>
					{/if}
					<div class="mt-1 flex flex-wrap gap-1">
						{#each provider.product_types ?? [] as wrapper (wrapper)}
							<Badge variant="outline" class="font-normal">{wrapper}</Badge>
						{/each}
						{#if isTransfer && provider.masterparty_id}
							<Badge variant="secondary" class="font-mono font-normal">
								{provider.masterparty_id}
							</Badge>
						{/if}
					</div>
				</div>
				{#if !provider.enabled}<Badge variant="destructive">Off</Badge>{/if}
			</button>
		{:else}
			<p class="text-muted-foreground text-sm">None yet.</p>
		{/each}
	</CardContent>
</Card>

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
		{#if creating || editing}
			{@const provider = editing}
			<Sheet.Header>
				<Sheet.Title>
					{provider
						? provider.name
						: isTransfer
							? 'New transfer provider'
							: 'New third-party provider'}
				</Sheet.Title>
				<Sheet.Description>
					{isTransfer
						? 'A ceding scheme. Its references are what a transfer instruction names it by.'
						: 'A provider whose products can be held alongside the firm’s own.'}
				</Sheet.Description>
			</Sheet.Header>
			<form
				method="POST"
				action="?/provider"
				use:enhance
				class="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pb-6"
			>
				<input type="hidden" name="kind" value={kind} />
				{#if provider}<input type="hidden" name="uid" value={provider.uid} />{/if}
				<div class="space-y-1">
					<Label for="p_name">Name</Label>
					<Input id="p_name" name="name" value={provider?.name ?? ''} required />
				</div>
				<div class="space-y-1">
					<Label for="p_description">Description</Label>
					<Textarea id="p_description" name="description" value={provider?.description ?? ''} />
				</div>
				{#if isTransfer}
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="space-y-1">
							<Label for="masterparty_id">Masterparty id</Label>
							<Input
								id="masterparty_id"
								name="masterparty_id"
								value={provider?.masterparty_id ?? ''}
								class="font-mono"
							/>
						</div>
						<div class="space-y-1">
							<Label for="origo_id">Origo id</Label>
							<Input
								id="origo_id"
								name="origo_id"
								value={provider?.origo_id ?? ''}
								class="font-mono"
							/>
						</div>
					</div>
				{/if}
				<!-- ONE wrapper (0130), not a set of the firm's own product ids. A third-party
				     provider provides one thing — an external SIPP, an offshore bond — and the
				     register says which (`data.wrapper_type`), so the pipeline maintains this and
				     the control is here for a provider the custodian does not publish. The old
				     checkbox column wrote `third_party_provider_products`, which modelled the same
				     fact twice and was the copy nobody read. -->
				<div class="space-y-1">
					<Label for="provider_product">Product</Label>
					<SelectMenu
						id="provider_product"
						name="product"
						items={wrapperOptions}
						value={provider?.product ?? ''}
						placeholder="Not stated"
					/>
					<p class="text-muted-foreground text-xs">
						What this provider provides. Refreshed from the register on every sync.
					</p>
				</div>
				<div class="flex items-center justify-between rounded border px-3 py-2">
					<Label for="p_enabled">Enabled</Label>
					<Switch id="p_enabled" name="enabled" checked={provider?.enabled ?? true} />
				</div>
				<div class="flex gap-2">
					<Button type="submit" disabled={!canManage}>Save provider</Button>
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
		{/if}
	</Sheet.Content>
</Sheet.Root>
