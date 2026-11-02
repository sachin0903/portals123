<script lang="ts">
	// Pension illustrations for this client — the record's "Illustrations" section.
	//
	// An illustration comes BEFORE an account (it is what the client is shown to decide whether to
	// open one), so the form works with no pension account at all and simply pre-fills from one when
	// it exists. The generate call POSTs to this same URL, which `+server.ts` answers.
	//
	// The crumb is the title. The listing, including New illustration, is the shared table.
	import { ListingSkeleton, Streamed } from '@graphene/ui';
	import { invalidateAll } from '$app/navigation';
	import Illustrations from '$lib/components/illustrations/illustrations.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const c = $derived(data.detail.contact);
</script>

<svelte:head><title>Illustrations · {data.clientName}</title></svelte:head>

<Streamed data={data.library} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton filters={0} pager={false} actions={data.canIllustrate} columns={6} />
	{/snippet}
	{#snippet children(library)}
		<Illustrations
			contactUid={c.uid}
			illustrations={library.illustrations}
			canIllustrate={data.canIllustrate}
			canShare={data.canShare}
			clientName={data.clientName}
			loadError={library.loadError}
			onretry={() => invalidateAll()}
		/>
	{/snippet}
</Streamed>
