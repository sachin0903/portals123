<script lang="ts">
	import { Alert, AlertDescription, Button, Input, Label, Segmented, Select } from '@graphene/ui';
	import TrashIcon from '@lucide/svelte/icons/trash-2';
	import type { TransfersStore } from '$lib/transfers/store.svelte';
	import type {
		Asset,
		TransferBasis,
		TransferRoute,
		TransferScope,
		Wrapper
	} from '$lib/transfers/types';

	let { wb }: { wb: TransfersStore } = $props();

	let client = $state('');
	let account = $state('');
	let provider = $state('');
	let plan = $state('');
	let value = $state<number | null>(null);
	let wrapper = $state<Wrapper>('SIPP');
	let route = $state<TransferRoute>('ml_sei');
	let basis = $state<TransferBasis>('cash');
	let scope = $state<TransferScope>('full');
	let lines = $state<{ isin: string; name: string; qty: number | null; price: number | null }[]>(
		[]
	);
	let created = $state<string | null>(null);

	const wrappers: Wrapper[] = ['SIPP', 'ISA', 'JISA', 'LISA', 'GIA'];

	function addLine() {
		lines.push({ isin: '', name: '', qty: null, price: null });
	}
	function removeLine(i: number) {
		lines.splice(i, 1);
	}

	function submit() {
		if (!client || !provider || !value) return;
		const assets: Asset[] =
			basis === 'in_specie'
				? lines
						.filter((l) => l.name)
						.map((l) => ({
							isin: l.isin || '—',
							name: l.name,
							qty: Number(l.qty ?? 0),
							price: Number(l.price ?? 0),
							value: Math.round(Number(l.qty ?? 0) * Number(l.price ?? 0) * 100) / 100,
							book: null,
							autoBook: false
						}))
				: [];
		wb.createTransfer({
			client,
			account: account || 'acc_new',
			firm: 'Your firm',
			route,
			provider,
			plan,
			wrapper,
			value: Number(value),
			basis,
			scope,
			assets
		});
		created = client;
		client = '';
		account = '';
		provider = '';
		plan = '';
		value = null;
		lines = [];
	}
</script>

<div class="space-y-4">
	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
		<div class="space-y-1.5">
			<Label>Client</Label><Input bind:value={client} placeholder="Client name" />
		</div>
		<div class="space-y-1.5">
			<Label>Account</Label><Input bind:value={account} placeholder="acc_…" />
		</div>
		<div class="space-y-1.5">
			<Label>Ceding provider</Label><Input bind:value={provider} placeholder="Aviva…" />
		</div>
		<div class="space-y-1.5">
			<Label>Plan number</Label><Input bind:value={plan} placeholder="AV-…" />
		</div>
	</div>

	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
		<div class="space-y-1.5">
			<Label>Wrapper</Label>
			<Select.Root type="single" bind:value={wrapper}>
				<Select.Trigger class="w-full">{wrapper}</Select.Trigger>
				<Select.Content>
					{#each wrappers as w (w)}<Select.Item value={w} label={w} />{/each}
				</Select.Content>
			</Select.Root>
		</div>
		<div class="space-y-1.5">
			<Label>Value (£)</Label><Input type="number" bind:value placeholder="0.00" />
		</div>
		<div class="space-y-1.5 lg:col-span-2">
			<Label>Route / custody</Label>
			<Segmented
				bind:value={route}
				options={[
					{ value: 'ml_sei', label: 'ML + SEI' },
					{ value: 'intra_sei', label: 'SEI only' },
					{ value: 'intra_seccl', label: 'Seccl only' }
				]}
			/>
		</div>
	</div>

	<div class="grid gap-3 sm:grid-cols-2">
		<div class="space-y-1.5">
			<Label>Basis</Label>
			<Segmented
				bind:value={basis}
				options={[
					{ value: 'cash', label: 'Cash' },
					{ value: 'in_specie', label: 'In-specie' }
				]}
			/>
		</div>
		<div class="space-y-1.5">
			<Label>Scope</Label>
			<Segmented
				bind:value={scope}
				options={[
					{ value: 'full', label: 'Full' },
					{ value: 'partial', label: 'Partial' }
				]}
			/>
		</div>
	</div>

	{#if basis === 'in_specie'}
		<div class="border-border/70 space-y-2 rounded-lg border p-3">
			<div class="flex items-center justify-between">
				<Label>In-specie asset lines (CREST re-registration)</Label>
				<Button size="sm" variant="outline" onclick={addLine}>Add line</Button>
			</div>
			{#each lines as line, i (i)}
				<div class="grid grid-cols-[1fr_1.5fr_0.7fr_0.7fr_auto] items-end gap-2">
					<div class="space-y-1">
						<Label class="text-[11px]">ISIN</Label><Input bind:value={line.isin} />
					</div>
					<div class="space-y-1">
						<Label class="text-[11px]">Name</Label><Input bind:value={line.name} />
					</div>
					<div class="space-y-1">
						<Label class="text-[11px]">Qty</Label><Input type="number" bind:value={line.qty} />
					</div>
					<div class="space-y-1">
						<Label class="text-[11px]">Price</Label><Input type="number" bind:value={line.price} />
					</div>
					<Button size="icon" variant="ghost" onclick={() => removeLine(i)}
						><TrashIcon class="size-4" /></Button
					>
				</div>
			{:else}
				<p class="text-muted-foreground text-xs">Add an asset line or switch to cash.</p>
			{/each}
		</div>
	{/if}

	{#if created}
		<Alert>
			<AlertDescription>
				Transfer recorded for <strong>{created}</strong> as a draft. Open it in the workbench to submit
				it down the route.
			</AlertDescription>
		</Alert>
	{/if}

	<div><Button onclick={submit}>Record transfer</Button></div>
</div>
