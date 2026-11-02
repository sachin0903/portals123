<script lang="ts">
	// The client record's overview band. The shared mount owns the header, drawers and
	// mutations; this page names the band, the title, and the leftover `?tab=` anchors
	// that still fold into this view (accounts, notes).
	import { afterNavigate, goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import { clientTabTarget } from '@graphene/ui/nav';
	import ClientRecordPage from '$lib/components/clients/client-record-page.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// A `?tab=accounts` / `?tab=notes` deep link lands HERE (those tabs are bands on the
	// overview). `?tab=details` is a route now and is redirected by the load.
	//
	// The scroll is handed to SvelteKit's own hash handling rather than done by hand: kit applies
	// its scroll restoration for the navigation after this hook (and after a frame), so
	// `scrollIntoView` here is silently undone. Navigating to `#{band}` also leaves the reader on
	// a canonical, shareable URL with the legacy `?tab=` dropped.
	afterNavigate(() => {
		const uid = page.params.uid;
		if (!uid) return;
		const target = clientTabTarget(uid, page.url.searchParams.get('tab'));
		if (!('anchor' in target)) return;
		void goto(`/clients/${uid}#${target.anchor}`, { replaceState: true, keepFocus: true });
	});
</script>

<svelte:head><title>{data.clientName} · Clients</title></svelte:head>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton kpis={4} cards={2} header={false} />
	{/snippet}
	{#snippet children(record)}
		<ClientRecordPage data={{ ...data, ...record }} section="overview" />
	{/snippet}
</Streamed>
