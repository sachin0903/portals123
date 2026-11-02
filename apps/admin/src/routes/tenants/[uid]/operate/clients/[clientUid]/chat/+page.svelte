<script lang="ts">
	import { LoadErrorBanner } from '@graphene/ui';
	import { MessagesInbox, type MessagesRoutes } from '@graphene/ui/book/messages';
	import { invalidateAll } from '$app/navigation';

	let { data } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/operate/messages/api`);
	const routes = $derived<MessagesRoutes>({
		conversations: `${base}/conversations`,
		search: (query) => `${base}/conversations${query ? `?q=${encodeURIComponent(query)}` : ''}`,
		contacts: (query) => `${base}/contacts${query ? `?q=${encodeURIComponent(query)}` : ''}`,
		groups: `${base}/groups`,
		conversation: (uid) => `${base}/conversations/${uid}/messages`,
		group: (uid) => `${base}/groups/${uid}/messages`,
		conversationAttachments: (uid) => `${base}/conversations/${uid}/attachments`,
		groupAttachments: (uid) => `${base}/groups/${uid}/attachments`,
		read: (uid) => `${base}/conversations/${uid}/read`,
		download: (conversationUid, attachmentUid) =>
			`${base}/conversations/${conversationUid}/attachments/${attachmentUid}`,
		events: `/tenants/${data.tenant.uid}/operate/messages/events`
	});
</script>

<svelte:head><title>Messages · {data.clientName}</title></svelte:head>

{#if data.loadError}
	<LoadErrorBanner
		message="This client's messages couldn't be loaded."
		onRetry={() => invalidateAll()}
	/>
{:else if data.conversation}
	<MessagesInbox
		conversations={[data.conversation]}
		groups={[]}
		canWrite={data.writing}
		showDirectory={false}
		{routes}
	/>
{:else}
	<p class="text-muted-foreground text-sm">This client does not have a conversation yet.</p>
{/if}
