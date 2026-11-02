<script lang="ts">
	// The same MessageDetail the firm-wide page renders; only the way BACK differs, and that is the
	// point of the route existing.
	import { page } from '$app/state';
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import PageTitle from '$lib/components/page-title.svelte';
	import { MessageDetail } from '@graphene/ui/comms';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The route's own param, not `data.client.uid`: this is the segment the browser is on, so the
	// link is right even mid-navigation.
	const back = $derived(`/clients/${page.params.uid}/emails`);
</script>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={1} header={false} />
	{/snippet}
	{#snippet children(record)}
		<PageTitle title="{record.message.subject} · {data.client.name}" />

		<!-- The page scrolls: the preview is a fixed pane and must not be clipped to the viewport the way
		     the listing shell is. Negative margin still cancels the shell padding so the edges line up. -->
		<div class="-m-4 flex flex-col gap-3 p-4 md:-m-6 md:p-6">
			<a
				class="text-muted-foreground shrink-0 text-sm underline-offset-2 hover:underline"
				href={back}
			>
				← {data.client.name}’s emails
			</a>
			<MessageDetail message={record.message} clientHref={(uid) => `/clients/${uid}`} />
		</div>
	{/snippet}
</Streamed>
