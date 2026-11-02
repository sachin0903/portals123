<script lang="ts">
	import { deserialize, enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Button,
		REFRESH_WHILE,
		RefreshWhile,
		Card,
		CardContent,
		ConfirmDialog,
		DataTable,
		Input,
		Label,
		LoadErrorBanner,
		PageHeader,
		SelectMenu,
		toast,
		type DataTableColumn,
		withToast
	} from '@graphene/ui';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	let shareOpen = $state(false);
	let shareUid = $state<string | null>(null);

	function askShare(uid: string): void {
		shareUid = uid;
		shareOpen = true;
	}

	async function confirmShare(): Promise<void> {
		const uid = shareUid;
		if (!uid) return;
		const body = new FormData();
		body.set('uid', uid);
		// Name this as a form action. A sibling POST endpoint would otherwise take the request.
		const res = await fetch('?/share', {
			method: 'POST',
			body,
			headers: { accept: 'application/json', 'x-sveltekit-action': 'true' }
		});
		const result = deserialize(await res.text());
		if (result.type !== 'success') {
			const message =
				result.type === 'failure'
					? String(result.data?.error ?? 'Could not share the illustration.')
					: 'Could not share the illustration.';
			toast.error(message);
			throw new Error(message);
		}
		toast.success('Shared with the client');
		shareUid = null;
		await invalidateAll();
	}
	type Row = PageData['illustrations'][number];
	const columns: DataTableColumn<Row>[] = [
		{ key: 'name', header: 'Illustration' },
		{ key: 'product', header: 'Wrapper' },
		{ key: 'status', header: 'Status' },
		{ key: 'created_at', header: 'Created', sortable: true, sortDefault: true, defaultDir: 'desc' },
		{ key: 'inputs', header: 'Assumptions' },
		{ key: 'share', header: '', align: 'right' }
	];
	const generating = $derived(data.illustrations.some((row) => row.status === 'generating'));
	const modelItems = $derived(
		data.models.map((model) => ({
			value: String(model.id),
			label: model.target_return == null ? `${model.name} (no target return)` : model.name
		}))
	);
</script>

<svelte:head><title>Illustrations · {data.clientName}</title></svelte:head>

<RefreshWhile
	active={generating}
	interval={REFRESH_WHILE.inFlightList}
	onpoll={() => invalidateAll()}
/>
<div class="space-y-5">
	<PageHeader
		title="Illustrations"
		description="Pension quotations from CTC, and GIA or ISA illustrations produced here, filed on this client."
	/>
	{#if data.loadError}<LoadErrorBanner message="Previous illustrations could not be loaded." />{/if}
	{#if data.writing}
		<Card glass>
			<CardContent>
				<form
					method="POST"
					action="?/generate"
					use:enhance={withToast('Illustration generated')}
					class="grid gap-3 md:grid-cols-2"
				>
					<div class="space-y-1.5">
						<Label for="model_id">Model</Label>
						<SelectMenu id="model_id" name="model_id" items={modelItems} />
					</div>
					<div class="space-y-1.5">
						<Label for="existing_fund_value">Existing fund value</Label>
						<Input id="existing_fund_value" name="existing_fund_value" type="number" min="0" />
					</div>
					<div class="space-y-1.5">
						<Label for="product_type">Product</Label>
						<SelectMenu
							id="product_type"
							name="product_type"
							value="SIPP"
							items={[
								{ value: 'SIPP', label: 'SIPP' },
								{ value: 'PENSION', label: 'Pension' },
								{ value: 'SSAS', label: 'SSAS' }
							]}
						/>
					</div>
					<div class="space-y-1.5">
						<Label for="exit_age">Retirement age</Label>
						<Input id="exit_age" name="exit_age" type="number" min="50" max="100" />
					</div>
					<input type="hidden" name="quotation_type" value="Accumulation" />
					<Button type="submit" class="md:col-span-2 md:w-fit">Generate illustration</Button>
				</form>
			</CardContent>
		</Card>
	{/if}
	<DataTable
		rows={data.illustrations}
		{columns}
		getKey={(row) => row.uid}
		label="Client illustrations"
		empty="No illustrations have been generated."
	>
		{#snippet cell(row, column)}
			{#if column.key === 'created_at'}
				{new Date(row.created_at).toLocaleDateString('en-GB')}
			{:else if column.key === 'product'}
				{row.product_type ?? String(row.inputs?.product_type ?? '—')}
			{:else if column.key === 'status'}
				{row.status ?? 'ready'}
			{:else if column.key === 'inputs'}
				{String(row.inputs?.quotation_type ?? 'Accumulation')}
			{:else if column.key === 'share'}
				{#if row.shared_with_client}
					<span class="text-muted-foreground text-sm">Shared</span>
				{:else if data.writing && row.document_uid && (row.status ?? 'ready') === 'ready'}
					<Button type="button" variant="ghost" size="sm" onclick={() => askShare(row.uid)}>
						Share
					</Button>
				{/if}
			{:else if row.document_uid}
				<a
					class="font-medium underline-offset-4 hover:underline"
					href={`/tenants/${data.tenant.uid}/operate/documents/${row.document_uid}`}
					>{row.name ?? 'Illustration'}</a
				>
			{:else}
				{row.name ?? 'Illustration'}
			{/if}
		{/snippet}
	</DataTable>
</div>

{#if data.writing}
	<ConfirmDialog
		bind:open={shareOpen}
		title={`Share this illustration with ${data.clientName}?`}
		description="It will appear in their portal."
		confirmLabel="Share"
		pendingLabel="Sharing…"
		onconfirm={confirmShare}
	/>
{/if}
