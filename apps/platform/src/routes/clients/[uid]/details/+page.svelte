<script lang="ts">
	// The client record's Details band — contact, bank, personal, pension and employer, with the
	// same header / advisers / compliance / notes chrome as Overview.
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import ClientRecordPage from '$lib/components/clients/client-record-page.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Details · {data.clientName}</title></svelte:head>

<!-- Absolute, not `../?/` — SvelteKit renders this route with no trailing slash after
     `details`, so a browser resolves `../` against `/clients/{uid}/details` as a FILE-style
     reference: it pops both `details` AND `{uid}`, landing on bare `/clients` (a 404, since the
     listing page has no `updateClient` action). An absolute path can't be ambiguous this way. -->
<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} header={false} />
	{/snippet}
	{#snippet children(record)}
		<ClientRecordPage
			data={{ ...data, ...record }}
			section="details"
			actionsPrefix={`/clients/${record.detail.contact.uid}?/`}
		/>
	{/snippet}
</Streamed>
