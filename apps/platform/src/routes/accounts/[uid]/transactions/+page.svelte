<script lang="ts">
	// The account record's transactions band. The record itself — header, drawers, triggers — is
	// `$lib/components/accounts/account-record.svelte`; this route names the band and the title.
	import AccountRecord from '$lib/components/accounts/account-record.svelte';
	import { invalidateAll } from '$app/navigation';
	import { ListingSkeleton, providerReference, Streamed } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const title = $derived(data.account.name ?? providerReference(data.account.uid) ?? 'Account');
</script>

<svelte:head><title>Transactions · {title}</title></svelte:head>

<Streamed data={data.band} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton fill={false} header={false} />
	{/snippet}
	{#snippet children(band)}
		<AccountRecord data={{ ...data, ...band }} section="transactions" />
	{/snippet}
</Streamed>
