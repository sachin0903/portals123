<script lang="ts">
	// The account record's overview band. The record itself — header, drawers, triggers — is
	// `$lib/components/accounts/account-record.svelte`; this route names the band and the title.
	import AccountRecord from '$lib/components/accounts/account-record.svelte';
	import { invalidateAll } from '$app/navigation';
	import { providerReference, RecordSkeleton, Streamed } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const title = $derived(data.account.name ?? providerReference(data.account.uid) ?? 'Account');
</script>

<svelte:head><title>Overview · {title}</title></svelte:head>

<Streamed data={data.band} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton kpis={4} cards={2} header={false} />
	{/snippet}
	{#snippet children(band)}
		<AccountRecord data={{ ...data, ...band }} section="overview" />
	{/snippet}
</Streamed>
