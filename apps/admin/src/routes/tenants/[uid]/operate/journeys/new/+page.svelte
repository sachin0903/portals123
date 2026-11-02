<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		EmptyState,
		Label,
		LoadErrorBanner,
		PageHeader,
		SelectMenu,
		withToast
	} from '@graphene/ui';
	import { page } from '$app/state';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const flowItems = $derived(data.flows.map((flow) => ({ value: flow.code, label: flow.name })));
	const clientItems = $derived(
		data.clients.map((client) => ({
			value: String(client.contact_id),
			label: client.name ?? client.email ?? `Client ${client.contact_id}`
		}))
	);
	const platformItems = $derived(
		data.platforms.map((platform) => ({ value: String(platform.id), label: platform.name }))
	);
	let selectedClientId = $state('');
	const selectedClientType = $derived(
		data.clients.find((client) => String(client.contact_id) === selectedClientId)?.type ?? null
	);
	const productItems = $derived(
		data.products
			.filter(
				(product) =>
					!selectedClientType || product.allowed_client_types.includes(selectedClientType)
			)
			.map((product) => ({
				value: String(product.id),
				label: `${product.name} (${product.product_type ?? 'product'})`
			}))
	);
</script>

<svelte:head><title>New journey · {data.tenant.name}</title></svelte:head>
<PageHeader
	title="New journey"
	back={{ href: `/tenants/${data.tenant.uid}/operate/journeys`, label: 'Journeys' }}
	description="Start a published journey for an existing client and one account."
/>
{#if data.loadError}
	<LoadErrorBanner message="Some journey options could not be loaded." />
{:else if !data.flows.length}
	<EmptyState
		title="No published journeys"
		description="This firm has no published flow that can be started."
	/>
{:else if data.writing}
	<form method="POST" use:enhance={withToast('Journey started')} class="mt-6 max-w-xl space-y-4">
		<div class="space-y-1.5">
			<Label for="flow_code">Journey</Label>
			<SelectMenu id="flow_code" name="flow_code" items={flowItems} />
		</div>
		<div class="space-y-1.5">
			<Label for="contact_id">Client</Label>
			<SelectMenu
				id="contact_id"
				name="contact_id"
				items={clientItems}
				bind:value={selectedClientId}
			/>
		</div>
		<div class="space-y-1.5">
			<Label for="platform_id">Platform</Label>
			<SelectMenu id="platform_id" name="platform_id" items={platformItems} />
		</div>
		<div class="space-y-1.5">
			<Label for="product_id">Product</Label>
			<SelectMenu id="product_id" name="product_id" items={productItems} />
		</div>
		<div class="space-y-1.5">
			<Label for="product_type">Wrapper</Label>
			<SelectMenu
				id="product_type"
				name="product_type"
				items={['SIPP', 'ISA', 'JISA', 'GIA'].map((value) => ({ value, label: value }))}
			/>
		</div>
		<Button type="submit">Start journey</Button>
	</form>
{:else}
	<!-- A refusal with nowhere to go. Nothing on this page is usable without the permission, so
	     the only honest control is the way back. -->
	<EmptyState
		title="Read-only workspace"
		description="Starting a journey on a firm's behalf takes write access to their book."
	>
		{#snippet action()}
			<Button variant="outline" href={`/tenants/${page.params.uid}/operate/journeys`}>
				Back to journeys
			</Button>
		{/snippet}
	</EmptyState>
{/if}
